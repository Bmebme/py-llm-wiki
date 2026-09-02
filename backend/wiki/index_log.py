"""Deterministic wiki index + ingest-log maintenance, ported from
llm_wiki src/lib/ingest.ts:

  buildDeterministicIngestLog (628-637)        — `## [YYYY-MM-DD] ingest | <source>`
  updateWikiIndexDeterministically (1555-1587) — add newly written pages to
                                                  wiki/index.md
  normalizeIndexTarget (1589-1594)
  updateBoundedRecentIndexSection (1596-1610)  — bounded `## Recently Updated`
  currentWikiDate (1805-1810)                  — local-timezone YYYY-MM-DD

All functions are synchronous and take explicit path/content arguments;
filesystem access goes through backend.core.file_service (FsError for
user-facing errors).
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from backend.core import file_service
from backend.core.file_service import FsError
from backend.wiki.frontmatter import parse_frontmatter
from backend.wiki.path_utils import get_file_name, normalize_path

# ingest.ts:59 — application-managed aggregate pages that never get an
# index entry of their own.
AGGREGATE_WIKI_PATHS = ["wiki/index.md", "wiki/overview.md", "wiki/log.md"]

_INDEX_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
_RECENT_SECTION = "## Recently Updated"
_RECENT_ENTRY_RE = re.compile(r"^-\s+")
_RECENT_MAX_ENTRIES = 200
_NEXT_SECTION_RE = re.compile(r"^##\s+")
_WIKI_PREFIX_RE = re.compile(r"^wiki/", re.IGNORECASE)
_MD_SUFFIX_RE = re.compile(r"\.md$", re.IGNORECASE)


def current_wiki_date(now: datetime.datetime | None = None) -> str:
    """Port of currentWikiDate (ingest.ts:1805-1810): local timezone,
    zero-padded YYYY-MM-DD (the TS uses getFullYear/getMonth/getDate)."""
    now = now or datetime.datetime.now()
    return f"{now.year:04d}-{now.month:02d}-{now.day:02d}"


def build_deterministic_ingest_log(existing: str, source_identity: str, date: str) -> str:
    """Port of buildDeterministicIngestLog (ingest.ts:628-637).

    Returns the full next log.md content: an append-only
    `## [YYYY-MM-DD] ingest | <source>` entry. An empty (or missing) log
    is bootstrapped with the `# Wiki Log` header.
    """
    entry = f"## [{date}] ingest | {source_identity}"
    if existing.strip():
        return f"{existing.rstrip()}\n\n{entry}\n"
    return f"# Wiki Log\n\n{entry}\n"


def append_ingest_log(project_path: str, source_identity: str, date: str) -> str:
    """Read wiki/log.md (defaulting to `# Wiki Log\\n` when absent) and
    append a deterministic ingest entry, mirroring the ingest.ts write at
    line 1297 (`tryReadFile` at 2529-2535 returns "" on failure, and
    buildDeterministicIngestLog bootstraps the header).

    Returns the new log content (also written to disk).
    """
    log_path = Path(project_path) / "wiki/log.md"
    try:
        existing = file_service.read_text(log_path)
    except FsError:
        existing = ""
    next_log = build_deterministic_ingest_log(existing, source_identity, date)
    file_service.write_text(log_path, next_log)
    return next_log


def normalize_index_target(target: str) -> str:
    """Port of normalizeIndexTarget (ingest.ts:1589-1594)."""
    normalized = normalize_path(target)
    normalized = _WIKI_PREFIX_RE.sub("", normalized, count=1)
    normalized = _MD_SUFFIX_RE.sub("", normalized, count=1)
    return normalized.lower()


def update_bounded_recent_index_section(index: str, additions: list[str]) -> str:
    """Port of updateBoundedRecentIndexSection (ingest.ts:1596-1610).

    Inserts `## Recently Updated` (or refreshes the existing one) with the
    new `- [[...]]` entries first, capped at 200 entries, preserving any
    sections that follow it.
    """
    lines = index.rstrip().split("\n")
    start = next((i for i, line in enumerate(lines) if line.strip() == _RECENT_SECTION), -1)

    prefix = lines[:start] if start >= 0 else lines

    if start >= 0:
        section_end = next(
            (i for i in range(start + 1, len(lines)) if _NEXT_SECTION_RE.match(lines[i])),
            -1,
        )
    else:
        section_end = -1

    if start >= 0:
        body = lines[start + 1:] if section_end < 0 else lines[start + 1:section_end]
        existing = [line for line in body if _RECENT_ENTRY_RE.match(line)]
    else:
        existing = []

    suffix = lines[section_end:] if section_end >= 0 else []

    # Order-preserving dedup (Array.from(new Set([...additions, ...existing]))),
    # then cap at 200.
    recent: list[str] = []
    seen: set[str] = set()
    for line in [*additions, *existing]:
        if line in seen:
            continue
        seen.add(line)
        recent.append(line)
    recent = recent[:_RECENT_MAX_ENTRIES]

    out = [*prefix, "", _RECENT_SECTION, *recent]
    if suffix:
        out += ["", *suffix]
    out.append("")
    return "\n".join(out)


def update_wiki_index_deterministically(project_path: str, written_paths: list[str]) -> bool:
    """Port of updateWikiIndexDeterministically (ingest.ts:1555-1587).

    Adds `- [[<target>]] — <title>` lines for newly written wiki pages to
    the `## Recently Updated` section of wiki/index.md. Returns False when
    there is nothing to add (no candidates, or every target is already in
    the index).

    Aggregate pages (wiki/index.md, wiki/overview.md, wiki/log.md) are
    excluded; titles come from frontmatter `title` (falling back to the
    file stem).
    """
    candidates: list[str] = []
    seen: set[str] = set()
    for path in written_paths:
        normalized = normalize_path(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        if (
            normalized.startswith("wiki/")
            and normalized.endswith(".md")
            and normalized not in AGGREGATE_WIKI_PATHS
        ):
            candidates.append(normalized)
    if not candidates:
        return False

    index_path = Path(project_path) / "wiki/index.md"
    try:
        index = file_service.read_text(index_path)
    except FsError:
        index = "# Wiki Index\n"

    known_targets = {
        normalize_index_target(match.group(1)) for match in _INDEX_LINK_RE.finditer(index)
    }

    additions: list[str] = []
    for path in candidates:
        target = _MD_SUFFIX_RE.sub("", path[len("wiki/"):], count=1)
        if normalize_index_target(target) in known_targets:
            continue
        try:
            content = file_service.read_text(Path(project_path) / path)
        except FsError:
            content = ""
        parsed = parse_frontmatter(content)
        frontmatter = parsed.frontmatter or {}
        title = frontmatter.get("title")
        if isinstance(title, str):
            title = title.strip()
        else:
            title = _MD_SUFFIX_RE.sub("", get_file_name(path), count=1)
        additions.append(f"- [[{target}]] — {title}")

    if not additions:
        return False

    file_service.write_text(index_path, update_bounded_recent_index_section(index, additions))
    return True
