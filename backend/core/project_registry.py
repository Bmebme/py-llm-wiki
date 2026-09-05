"""Project registry — the port of api_server.rs load_projects/resolve_project.

Projects come from app-state.json `projectRegistry` (uuid → {name, path})
and `recentProjects` ([{name, path}]), plus a session-level "current"
pointer. The clip-server source is not applicable in the web port.
"""

from __future__ import annotations

from pathlib import Path

from backend.core import project as project_core
from backend.core import settings_store
from backend.core.file_service import FsError, normalize_path

_current_project_path: str | None = None


def set_current(path: str) -> None:
    global _current_project_path
    _current_project_path = normalize_path(path)


def current_project_path() -> str:
    return _current_project_path or ""


def list_projects() -> list[dict]:
    """Port of load_projects (api_server.rs:732-811), minus clip-server."""
    current = current_project_path()
    by_path: dict[str, dict] = {}
    state = settings_store.load() or {}

    registry = state.get("projectRegistry")
    if isinstance(registry, dict):
        for pid, value in registry.items():
            path = value.get("path") if isinstance(value, dict) else None
            if not path:
                continue
            path = normalize_path(str(path))
            name = value.get("name") if isinstance(value, dict) else None
            name = str(name) if name else project_core.project_name_from_path(path)
            by_path[path] = _entry(pid, name, path, path == current)

    recents = state.get("recentProjects")
    if isinstance(recents, list):
        for value in recents:
            if not isinstance(value, dict):
                continue
            path = value.get("path")
            if not path:
                continue
            path = normalize_path(str(path))
            if path in by_path:
                continue
            pid = project_core.read_project_id(path) or path
            name = value.get("name") or project_core.project_name_from_path(path)
            by_path[path] = _entry(pid, str(name), path, path == current)

    if current and current not in by_path:
        pid = project_core.read_project_id(current) or current
        by_path[current] = _entry(
            pid, project_core.project_name_from_path(current), current, True
        )

    # 过滤路径已不存在的死项目 (目录被删后 registry 残留会导致
    # open 报 "Path does not exist", 且 UI 仍展示无法打开的条目)
    alive = {path: entry for path, entry in by_path.items() if Path(path).exists()}
    return sorted(alive.values(), key=lambda p: p["name"].lower())


def _entry(pid: str, name: str, path: str, current: bool) -> dict:
    return {"id": pid, "name": name, "path": path, "current": current}


def resolve_project(project_id: str) -> dict:
    """Port of resolve_project (api_server.rs:813-824). Accepts uuid,
    absolute path, or 'current'."""
    wants_current = project_id.lower() == "current"
    for project in list_projects():
        if (
            project["id"] == project_id
            or _path_matches(project["path"], project_id)
            or (wants_current and project["current"])
        ):
            return project
    raise FsError(f"Unknown project: {project_id}")


def _path_matches(stored_path: str, candidate: str) -> bool:
    return normalize_path(stored_path) == normalize_path(candidate)


def register(project_id: str, name: str, path: str, make_current: bool = True) -> None:
    """Add to projectRegistry + recentProjects in app-state.json."""
    path = normalize_path(path)

    def mutator(state: dict) -> None:
        registry = state.setdefault("projectRegistry", {})
        registry[project_id] = {"name": name, "path": path}
        recents = state.get("recentProjects")
        if not isinstance(recents, list):
            recents = []
            state["recentProjects"] = recents
        recents[:] = [r for r in recents if normalize_path(r.get("path", "")) != path]
        recents.insert(0, {"name": name, "path": path})
        del recents[10:]
        if make_current:
            state["lastProject"] = path

    settings_store.update(mutator)
    if make_current:
        set_current(path)


def find_owner_project(abs_path: str) -> dict:
    """Resolve which registered project contains an absolute path.

    Used by the browser fs shim, which passes the same absolute paths
    the desktop app used. Longest matching root wins.
    """
    target = Path(abs_path)
    best: dict | None = None
    best_len = -1
    for project in list_projects():
        root = Path(project["path"])
        try:
            target.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        depth = len(root.parts)
        if depth > best_len:
            best = project
            best_len = depth
    if best is None:
        raise FsError(f"No open project contains path: {abs_path}")
    return best
