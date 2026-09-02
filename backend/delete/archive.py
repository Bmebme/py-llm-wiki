"""Project archive export/import — port of llm_wiki
src-tauri/src/commands/project_maintenance.rs (122-230).

Export: deflated zip of the whole project tree (symlinks skipped,
destination must live OUTSIDE the project). Import: unzip into a fresh
project folder, then validate + register like open_project.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from backend.core.file_service import FsError


def resolve_export_destination(root: Path, output: Path) -> Path:
    """Containment check: the archive must not live inside the project."""
    try:
        resolved = output.resolve() if output.exists() else (
            output.parent.resolve() / output.name
        )
    except OSError as exc:
        raise FsError(str(exc)) from exc
    if resolved == root or root in resolved.parents:
        raise FsError("Export destination must be outside the project directory")
    return resolved


def export_project_archive(project_path: str, destination: str) -> None:
    if not Path(project_path).is_absolute() or not Path(destination).is_absolute():
        raise FsError("Project and archive paths must be absolute")
    try:
        root = Path(project_path).resolve()
    except OSError as exc:
        raise FsError(str(exc)) from exc
    output = resolve_export_destination(root, Path(destination))

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in sorted(root.rglob("*")):
            if entry == root or entry.is_symlink():
                continue
            rel = entry.relative_to(root).as_posix()
            if entry.is_dir():
                archive.writestr(f"{rel}/", "")
            else:
                archive.write(entry, rel)


def rebuild_wiki_index(project_path: str) -> dict:
    """Regenerate wiki/index.md from the pages on disk — port of
    project_maintenance.rs rebuild_wiki_index (287+). Pages grouped by
    type in template section order; unknown types get their own section."""
    from backend.search.graph import extract_type
    from backend.search.scoring import extract_title

    root = Path(project_path)
    wiki_root = root / "wiki"
    groups: dict[str, list[tuple[str, str]]] = {}
    if wiki_root.exists():
        for entry in sorted(wiki_root.rglob("*.md")):
            if not entry.is_file() or entry.name in ("index.md", "log.md", "overview.md"):
                continue
            try:
                content = entry.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            slug = entry.relative_to(wiki_root).as_posix().removesuffix(".md")
            title = extract_title(content, entry.name)
            page_type = extract_type(content)
            groups.setdefault(page_type, []).append((slug, title))

    section_order = ["entity", "concept", "source", "query", "comparison", "synthesis"]
    lines = ["# Wiki Index", ""]
    emitted: set[str] = set()
    sections = 0
    for page_type in section_order:
        entries = groups.pop(page_type, [])
        lines.extend([f"## {page_type.title()}s", ""])
        if entries:
            sections += 1
            for slug, title in entries:
                lines.append(f"- [[{slug}]] — {title}")
                emitted.add(slug)
        lines.append("")
    for page_type, entries in sorted(groups.items()):
        sections += 1
        lines.extend([f"## {page_type.title()}s", ""])
        for slug, title in entries:
            lines.append(f"- [[{slug}]] — {title}")
            emitted.add(slug)
        lines.append("")

    from backend.core.file_service import write_text

    write_text(wiki_root / "index.md", "\n".join(lines))
    return {"pages": len(emitted), "groups": sections}


def import_project_archive(archive_path: str, destination: str) -> str:
    """Unzip into destination (the new project root) and return its path.

    Zip-slip guarded: member names must stay inside the destination.
    """
    archive = Path(archive_path)
    if not archive.is_file():
        raise FsError(f"Archive does not exist: '{archive_path}'")
    dest_root = Path(destination)
    if dest_root.exists() and any(dest_root.iterdir()):
        raise FsError(f"Destination is not empty: '{destination}'")
    dest_root.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            target = (dest_root / member.filename).resolve()
            if dest_root.resolve() not in target.parents and target != dest_root.resolve():
                raise FsError(f"Archive entry escapes the destination: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as source, target.open("wb") as out:
                    while chunk := source.read(65536):
                        out.write(chunk)
    return dest_root.as_posix()
