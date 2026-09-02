"""Source-file deletion cascade — port of llm_wiki
src/lib/source-lifecycle.ts deleteSourceFiles (404-522).

Three-way matching: sources[] frontmatter identity match (with legacy
basename fallback), then the shared wikilink/title sweep from
wiki_page_delete. Pages still citing other sources keep those sources
(shared-entity preservation); pages left with zero sources are deleted.
"""

from __future__ import annotations

from pathlib import Path

from backend.core.file_service import delete_file, read_text, write_text
from backend.delete.wiki_page_delete import cascade_delete_wiki_pages_with_refs
from backend.ingest import cache as ingest_cache
from backend.wiki.frontmatter import parse_frontmatter
from backend.wiki.sources_merge import parse_sources, write_sources
from backend.wiki.source_identity import source_reference_identity


def _matches_deleted(page_source: str, deleted_identities: list[str]) -> bool:
    """Port of sourceNameMatchesAny (source-lifecycle.ts:666-682):
    source-reference identity equality with a legacy basename fallback."""
    ref = source_reference_identity(page_source)
    ref_key = ref.lower()
    for identity in deleted_identities:
        identity_ref = source_reference_identity(identity)
        identity_key = identity_ref.lower()
        if ref_key == identity_key:
            return True
        # Legacy fallback: pages that stored bare filenames match the
        # deleted source's basename.
        if "/" not in ref and Path(ref).name.lower() == Path(identity_ref).name.lower():
            return True
    return False


def delete_source_files(project_path: str, source_identities: list[str]) -> dict:
    """Delete raw source files and cascade into the wiki. Returns a
    summary: {deletedSources, deletedWikiPages, rewrittenSourcesPages}."""
    pp = project_path.rstrip("/")
    deleted_identities = [identity for identity in source_identities if identity]

    # 1. Remove the raw files + their cache entries.
    for identity in deleted_identities:
        raw_path = Path(pp) / "raw" / "sources" / identity
        try:
            delete_file(raw_path)
        except Exception as exc:  # noqa: BLE001 - best effort, logged
            print(f"[delete] failed to delete raw source {identity}: {exc}")
        ingest_cache.remove_from_ingest_cache(pp, identity)

    # 2. Scan wiki pages: split into doomed (no surviving sources) and
    #    surviving (rewrite the sources array).
    doomed: list[str] = []
    surviving_rewrites: dict[str, str] = {}
    wiki_root = Path(pp) / "wiki"
    if wiki_root.exists():
        for entry in wiki_root.rglob("*.md"):
            if not entry.is_file():
                continue
            try:
                content = entry.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            sources = parse_sources(content)
            if not sources:
                # Pages without a sources field are unowned — skipped
                # (source-lifecycle.ts:478-481).
                continue
            surviving = [
                s for s in sources if not _matches_deleted(s, deleted_identities)
            ]
            if surviving:
                surviving_rewrites[str(entry)] = write_sources(content, surviving)
            else:
                doomed.append(str(entry))

    for entry_path, updated in surviving_rewrites.items():
        try:
            write_text(Path(entry_path), updated)
        except Exception as exc:  # noqa: BLE001
            print(f"[delete] failed to rewrite {entry_path}: {exc}")

    # 3. Cascade-delete the doomed pages + sweep references.
    cascade_result = cascade_delete_wiki_pages_with_refs(pp, doomed) if doomed else {
        "deletedPaths": [], "rewrittenFiles": 0,
    }

    # 4. Log entry (mirrors appendSourceDeleteLog's intent).
    from datetime import date

    log_path = Path(pp) / "wiki" / "log.md"
    try:
        existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        entries = "\n".join(
            f"## [{date.today().isoformat()}] source-delete | {identity}"
            for identity in deleted_identities
        )
        write_text(log_path, f"{existing}\n\n{entries}".strip() + "\n")
    except Exception:  # noqa: BLE001
        pass

    return {
        "deletedSources": deleted_identities,
        "deletedWikiPages": cascade_result["deletedPaths"],
        "rewrittenSourcesPages": len(surviving_rewrites),
        "rewrittenFiles": cascade_result["rewrittenFiles"],
    }
