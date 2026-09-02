"""Project commands — port of llm_wiki src-tauri/src/commands/project.rs
create/open, plus registry bookkeeping the desktop app did client-side."""

from __future__ import annotations

from backend.core import project as project_core
from backend.core import project_registry
from backend.core.file_service import FsError

COMMANDS: dict[str, callable] = {}


def command(name: str):
    def decorator(func):
        COMMANDS[name] = func
        return func

    return decorator


@command("create_project")
def create_project(name: str, path: str) -> dict:
    try:
        project = project_core.create_project(name, path)
        pid = project_core.ensure_project_id(project.path)
        project_registry.register(pid, project.name, project.path)
    except FsError as exc:
        raise FsError(str(exc)) from exc
    return {"id": pid, "name": project.name, "path": project.path}


@command("open_project")
def open_project(path: str) -> dict:
    try:
        project = project_core.open_project(path)
        pid = project_core.ensure_project_id(project.path)
        project_registry.register(pid, project.name, project.path)
    except FsError as exc:
        raise FsError(str(exc)) from exc
    return {"id": pid, "name": project.name, "path": project.path}


@command("open_project_folder")
def open_project_folder(path: str) -> None:
    # Browser cannot open an OS file manager; the desktop shim disables
    # the button, this is the safety net.
    return None


@command("open_path_in_project")
def open_path_in_project(projectPath: str, targetPath: str) -> None:
    return None
