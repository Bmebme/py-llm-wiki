"""Cascade wiki-page deletion — port of llm_wiki
src/lib/wiki-page-delete.ts cascadeDeleteWikiPagesWithRefs.

Order: read titles first → delete each page (+ media cascade for source
pages) → sweep surviving wiki/*.md (index listing entries, body
wikilinks, `related:` frontmatter arrays).
"""

from __future__ import annotations

from pathlib import Path

from backend.core.file_service import FsError, read_text, write_text_atomic
from backend.delete.wiki_cleanup import (
    build_deleted_keys,
    clean_index_listing,
    extract_frontmatter_title,
    strip_deleted_wikilinks,
)
from backend.wiki.sources_merge import (
    parse_frontmatter_array,
    write_frontmatter_array,
)


def is_source_page(page_path: str) -> bool:
    return "/sources/" in page_path.replace("\\", "/")


def get_file_stem(page_path: str) -> str:
    return Path(page_path).stem


def cascade_delete_wiki_page(project_path: str, page_path: str) -> None:
    """Delete one wiki page + its media dir (source pages only)."""
    from backend.core.file_service import delete_file

    abs_path = Path(page_path)
    delete_file(abs_path)
    slug = get_file_stem(page_path)

    if is_source_page(page_path) and slug and not slug.startswith("."):
        media_dir = Path(project_path) / "wiki" / "media" / slug
        try:
            delete_file(media_dir)
        except FsError:
            pass  # most common: never existed — not an error


def cascade_delete_wiki_pages_with_refs(
    project_path: str, page_paths: list[str]
) -> dict:
    """Port of cascadeDeleteWikiPagesWithRefs (wiki-page-delete.ts:161-245)."""
    pp = project_path.rstrip("/")
    infos: list[dict] = []
    for page_path in page_paths:
        title = ""
        try:
            content = read_text(Path(page_path))
            title = extract_frontmatter_title(content)
        except FsError:
            pass
        infos.append({
            "slug": get_file_stem(page_path),
            "title": title,
            "path": page_path,
        })

    deleted_paths: list[str] = []
    for info in infos:
        try:
            cascade_delete_wiki_page(pp, info["path"])
            deleted_paths.append(info["path"])
        except FsError as exc:  # noqa: BLE001 - one failure must not abort the rest
            print(f"[delete] failed to delete {info['path']}: {exc}")

    deleted_keys = build_deleted_keys(infos)
    rewritten_files = 0

    wiki_root = Path(pp) / "wiki"
    if wiki_root.exists():
        for entry in wiki_root.rglob("*.md"):
            if not entry.is_file():
                continue
            try:
                content = entry.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            updated = content
            if entry.name == "index.md":
                updated = clean_index_listing(updated, deleted_keys)
            else:
                updated = strip_deleted_wikilinks(updated, deleted_keys)
                # Strip deleted slugs from the related: frontmatter array.
                related = parse_frontmatter_array(updated, "related")
                if related:
                    filtered = [v for v in related if _related_key(v) not in deleted_keys]
                    if filtered != related:
                        updated = write_frontmatter_array(updated, "related", filtered)
            if updated != content:
                try:
                    write_text_atomic(entry, updated)
                    rewritten_files += 1
                except FsError:
                    pass

    return {"deletedPaths": deleted_paths, "rewrittenFiles": rewritten_files}


def _related_key(value: str) -> str:
    from backend.delete.wiki_cleanup import normalize_wiki_ref_key

    return normalize_wiki_ref_key(value)
