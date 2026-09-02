"""File commands — port of llm_wiki src-tauri/src/commands/fs.rs.

Semantics match the Rust commands exactly:
- absolute paths in/out (frontend composes paths itself)
- list_directory: dirs first, then alphabetical; hidden entries filtered
  unless includeHidden; children omitted when empty; max_depth 1..=30
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path

from backend.core import file_service
from backend.core import project_registry
from backend.core.file_service import FsError

COMMANDS: dict[str, callable] = {}


def command(name: str):
    def decorator(func):
        COMMANDS[name] = func
        return func

    return decorator


def _abs(path: str) -> Path:
    """Validate absolute path lives inside a registered project (the
    containment check the desktop OS gave us)."""
    if not os.path.isabs(path):
        raise FsError("Path must be absolute")
    project = project_registry.find_owner_project(path)
    return file_service.resolve_abs(project["path"], path)


# --- reads ---------------------------------------------------------------


@command("read_file")
def read_file(path: str, extractImages: bool | None = None) -> str:
    # extractImages feeds the vision pipeline (deferred); accepted for
    # signature compatibility.
    return file_service.read_text(_abs(path))


@command("read_file_as_base64")
def read_file_as_base64(path: str) -> dict:
    abs_path = _abs(path)
    try:
        raw = abs_path.read_bytes()
    except OSError as exc:
        raise FsError(f"Failed to read file '{path}': {exc}") from exc
    mime_type = mimetypes.guess_type(abs_path.name)[0] or "application/octet-stream"
    return {"base64": base64.b64encode(raw).decode("ascii"), "mimeType": mime_type}


@command("preprocess_file")
def preprocess_file(path: str) -> str:
    """Extract text from a source file (PDF/DOCX/Org/text) via
    backend/ingest/extract_text.py — the web equivalent of the Rust
    preprocess_file command."""
    from backend.ingest.extract_text import extract_text

    return extract_text(_abs(path))


# --- writes --------------------------------------------------------------


@command("write_file")
def write_file(path: str, contents: str) -> None:
    file_service.write_text(_abs(path), contents)


@command("write_file_atomic")
def write_file_atomic(path: str, contents: str) -> None:
    file_service.write_text_atomic(_abs(path), contents)


@command("write_file_base64")
def write_file_base64(path: str, base64_data: str) -> None:
    abs_path = _abs(path)
    try:
        raw = base64.b64decode(base64_data)
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(raw)
    except (OSError, ValueError) as exc:
        raise FsError(f"Failed to write file '{path}': {exc}") from exc


@command("create_directory")
def create_directory(path: str) -> None:
    file_service.create_directory(_abs(path))


@command("delete_file")
def delete_file(path: str) -> None:
    file_service.delete_file(_abs(path))


@command("copy_file")
def copy_file(source: str, destination: str) -> None:
    file_service.copy_path(_abs(source), _abs(destination))


@command("copy_directory")
def copy_directory(source: str, destination: str) -> list[str]:
    created = file_service.copy_path(_abs(source), _abs(destination))
    return created or []


# --- listing -------------------------------------------------------------


def _entry_is_visible(name: str, include_hidden: bool) -> bool:
    if name.startswith("."):
        return include_hidden
    return True


def _build_tree(directory: Path, depth: int, max_depth: int, include_hidden: bool) -> list[dict]:
    """Port of fs.rs build_tree: dirs first, alphabetical within groups,
    children omitted when empty, forward-slash absolute paths."""
    if depth >= max_depth:
        return []
    try:
        entries = sorted(
            (e for e in directory.iterdir() if _entry_is_visible(e.name, include_hidden)),
            key=lambda p: (not p.is_dir(), p.name),
        )
    except OSError as exc:
        raise FsError(f"Failed to read directory '{directory}': {exc}") from exc

    nodes: list[dict] = []
    for entry in entries:
        is_dir = entry.is_dir()
        children = (
            _build_tree(entry, depth + 1, max_depth, include_hidden) if is_dir else None
        )
        if is_dir and not children:
            children = None
        node: dict = {"name": entry.name, "path": entry.as_posix(), "is_dir": is_dir}
        if children is not None:
            node["children"] = children
        nodes.append(node)
    return nodes


@command("list_directory")
def list_directory(
    path: str,
    includeHidden: bool | None = None,
    maxDepth: int | None = None,
) -> list[dict]:
    include_hidden = bool(includeHidden)
    max_depth = min(max(1, maxDepth or 30), 30)
    abs_path = _abs(path)
    if not abs_path.exists():
        raise FsError(f"Path does not exist: '{path}'")
    if not abs_path.is_dir():
        raise FsError(f"Path is not a directory: '{path}'")
    return _build_tree(abs_path, 0, max_depth, include_hidden)


# --- stat ----------------------------------------------------------------


@command("file_exists")
def file_exists(path: str) -> bool:
    return _abs(path).exists()


@command("get_file_modified_time")
def get_file_modified_time(path: str) -> int:
    return file_service.file_stat(_abs(path))["modifiedTime"]


@command("get_file_size")
def get_file_size(path: str) -> int:
    return file_service.file_stat(_abs(path))["size"]


@command("get_file_md5")
def get_file_md5(path: str) -> str:
    return file_service.file_stat(_abs(path))["md5"]


# --- wiki page helpers (deferred to M2/M5) -------------------------------


@command("find_related_wiki_pages")
def find_related_wiki_pages(projectPath: str, sourceName: str) -> list[str]:
    return []  # M5: cascade-delete support


@command("get_page_links")
def get_page_links(projectPath: str, filePath: str) -> dict:
    return {"outgoing": [], "backlinks": [], "missing": []}  # M2


@command("create_missing_wiki_page")
def create_missing_wiki_page(projectPath: str, title: str, content: str | None = None) -> str:
    raise FsError("create_missing_wiki_page is not implemented yet (M4)")


@command("apply_text_selection_edit")
def apply_text_selection_edit(
    projectPath: str,
    filePath: str,
    prefix: str,
    selectedText: str,
    suffix: str,
    replacement: str,
) -> str:
    raise FsError("apply_text_selection_edit is not implemented yet (M5)")


# --- file history (deferred to M5) ----------------------------------------


@command("get_file_history_settings")
def get_file_history_settings(projectPath: str) -> dict:
    return {"enabled": False, "maxVersionsPerFile": 0}


@command("set_file_history_settings")
def set_file_history_settings(projectPath: str, settings: dict) -> dict:
    return settings


@command("get_file_history_stats")
def get_file_history_stats(projectPath: str) -> dict:
    return {"bytes": 0, "files": 0, "entries": 0}


@command("clear_file_history")
def clear_file_history(projectPath: str) -> None:
    return None


@command("list_file_history")
def list_file_history(projectPath: str, filePath: str) -> list[dict]:
    return []


@command("restore_file_history")
def restore_file_history(projectPath: str, filePath: str, entryId: str) -> str:
    raise FsError("restore_file_history is not implemented yet (M5)")
