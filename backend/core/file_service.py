"""Filesystem layer for project files.

Port of the path-safety and listing logic from llm_wiki's
src-tauri/src/api_server.rs (safe_join, is_public_project_rel,
is_text_content_rel, list_tree, list_public_roots) plus the plain
file operations the Rust fs commands provided.

All API-facing functions take project-relative paths ("wiki/index.md")
or absolute paths that resolve inside a known project; internal modules
may use absolute paths directly (mirroring the Rust command split).
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path, PurePosixPath

from backend import config

TEXT_EXTENSIONS = {
    "md", "mdx", "txt", "csv", "json", "yaml", "yml", "xml", "html", "htm", "log",
}

PUBLIC_ROOTS = ["purpose.md", "schema.md", "wiki", "raw/sources"]


class FsError(Exception):
    """Raised with the same message strings the desktop app returned."""


def normalize_path(path: str) -> str:
    return path.replace("\\", "/").rstrip("/")


def safe_join(project_path: str, rel: str) -> Path:
    """Port of api_server.rs safe_join (948-988)."""
    root = Path(project_path)
    rel = rel.lstrip("/")
    rel_path = PurePosixPath(rel)
    if rel_path.is_absolute():
        raise FsError("Absolute paths are not allowed")
    for part in rel_path.parts:
        if part in ("..", "/"):
            raise FsError("Path traversal is not allowed")
    joined = root.joinpath(*rel_path.parts)
    root_canon = _canonical(root, f"Failed to resolve project path")
    if joined.exists():
        joined_canon = _canonical(joined, "Failed to resolve path")
        if not _is_within(joined_canon, root_canon):
            raise FsError("Resolved path escapes the project directory")
        return joined_canon
    parent = joined.parent
    if parent.exists():
        parent_canon = _canonical(parent, "Failed to resolve parent path")
        if not _is_within(parent_canon, root_canon):
            raise FsError("Resolved parent escapes the project directory")
    return joined


def _canonical(path: Path, message: str) -> Path:
    try:
        return path.resolve()
    except OSError as exc:  # pragma: no cover - OS-specific
        raise FsError(f"{message}: {exc}") from exc


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def is_public_project_rel(rel: str) -> bool:
    """Port of api_server.rs is_public_project_rel (990-1003)."""
    rel = normalize_path(rel).lstrip("/")
    if any(part == "" or part.startswith(".") for part in rel.split("/")):
        return False
    lower = rel.lower()
    return (
        lower == "purpose.md"
        or lower == "schema.md"
        or lower.startswith("wiki/")
        or lower.startswith("raw/sources/")
    )


def is_text_content_rel(rel: str) -> bool:
    ext = Path(normalize_path(rel).lower()).suffix.lstrip(".")
    return ext in TEXT_EXTENSIONS


class FileNode(dict):
    """ApiFileNode: {name, path, is_dir, size?, children?} — camelCase keys."""


def _file_node(name: str, path: str, is_dir: bool, size: int | None, children: list | None) -> dict:
    node: dict = {"name": name, "path": path, "is_dir": is_dir}
    if is_dir:
        if children is not None:
            node["children"] = children
    else:
        node["size"] = size
    return node


def list_public_roots(project_path: str, recursive: bool, max_files: int) -> list[dict]:
    """Port of api_server.rs list_public_roots (1027-1049)."""
    count = count_ref()
    roots: list[dict] = []
    for rel in PUBLIC_ROOTS:
        path = safe_join(project_path, rel)
        if not path.exists():
            continue
        node = _push_file_node(project_path, path, recursive, max_files, count)
        if node is not None:
            roots.append(node)
    return roots


def count_ref(initial: int = 0) -> dict:
    """Mutable int cell shared across the recursive listing (mirrors &mut usize)."""
    return {"v": initial}


def list_tree(project_path: str, path: Path, recursive: bool, max_files: int, count: dict) -> list[dict]:
    out: list[dict] = []
    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        raise FsError(f"Failed to list directory: {exc}") from exc
    for entry in entries:
        node = _push_file_node(project_path, entry, recursive, max_files, count)
        if node is not None:
            out.append(node)
    return out


def _push_file_node(project_path: str, path: Path, recursive: bool, max_files: int, count: dict) -> dict | None:
    """Returns None for entries that must be skipped (dotfiles, symlinks)."""
    name = path.name
    if name.startswith("."):
        return None
    try:
        is_symlink = path.is_symlink()
        is_dir = path.is_dir()
        meta_size = path.stat().st_size
    except OSError as exc:
        raise FsError(f"Failed to read metadata: {exc}") from exc
    if is_symlink:
        return None
    count["v"] += 1
    if count["v"] > max_files:
        raise FsError(f"File listing exceeds maxFiles limit ({max_files})")
    children = list_tree(project_path, path, True, max_files, count) if (recursive and is_dir) else None
    return _file_node(
        name,
        relative_to_project(project_path, path),
        is_dir,
        None if is_dir else meta_size,
        children,
    )


def relative_to_project(project_path: str, path: Path) -> str:
    root = Path(project_path)
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


# --- Plain file operations (Rust commands/fs.rs equivalents) ---


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise FsError("File is not valid UTF-8 text") from exc
    except OSError as exc:
        raise FsError(f"Failed to read file '{path}': {exc}") from exc


def write_text(path: Path, contents: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    except OSError as exc:
        raise FsError(f"Failed to write file '{path}': {exc}") from exc


def write_text_atomic(path: Path, contents: str) -> None:
    """writeFileAtomic: temp + rename (fs.rs write_file_atomic)."""
    tmp = path.with_name(path.name + ".tmp")
    write_text(tmp, contents)
    tmp.replace(path)


def delete_file(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as exc:
        raise FsError(f"Failed to delete '{path}': {exc}") from exc


def copy_path(source: Path, destination: Path) -> list[str] | None:
    """copyFile / copyDirectory. Returns created paths for dirs."""
    try:
        if source.is_dir():
            created: list[str] = []
            for item in source.rglob("*"):
                rel = item.relative_to(source)
                target = destination / rel
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target)
                    created.append(target.as_posix())
            return created
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return None
    except OSError as exc:
        raise FsError(f"Failed to copy '{source}': {exc}") from exc


def create_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FsError(f"Failed to create directory '{path}': {exc}") from exc


def file_stat(path: Path) -> dict:
    """{exists, modifiedTime (epoch ms), size, md5} — folds fs.rs stat commands."""
    if not path.exists():
        return {"exists": False, "modifiedTime": None, "size": None, "md5": None}
    st = path.stat()
    return {
        "exists": True,
        "modifiedTime": int(st.st_mtime * 1000),
        "size": st.st_size,
        "md5": _md5(path),
    }


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_abs(project_path: str, absolute: str) -> Path:
    """Validate an absolute path lives inside the project, return it.

    The browser shim passes the absolute paths the desktop app used;
    this is the containment check that desktop OS isolation gave us.
    """
    if not os.path.isabs(absolute):
        raise FsError("Path must be absolute")
    resolved = _canonical(Path(absolute), "Failed to resolve path")
    root = _canonical(Path(project_path), "Failed to resolve project path")
    if not _is_within(resolved, root):
        raise FsError("Resolved path escapes the project directory")
    return resolved
