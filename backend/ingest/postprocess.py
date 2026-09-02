"""Ingest post-processing — ports from llm_wiki src/lib/ingest.ts.

buildFallbackSourceSummary (1812-1836), stampGeneratedFrontmatterDates
(1838-1849), stampGeneratedLogDate (1849+), canonicalizeSourcesField
(1624+), isValidSourceReference (1624 area).
"""

from __future__ import annotations

import re

FRONTMATTER_RE = re.compile(r"^(---\s*\r?\n)([\s\S]*?)(\r?\n---\s*(?:\r?\n|$))")

AGGREGATE_WIKI_PATHS = ["wiki/index.md", "wiki/overview.md", "wiki/log.md"]


def build_fallback_source_summary(source_identity: str, analysis: str, date: str) -> str:
    """Guaranteed source summary when the LLM produced no source page."""
    return "\n".join([
        "---",
        "type: source",
        f'title: "Source: {source_identity}"',
        f"created: {date}",
        f"updated: {date}",
        f'sources: ["{source_identity}"]',
        "tags: []",
        "related: []",
        "---",
        "",
        f"# Source: {source_identity}",
        "",
        # Recovery page: preserving the complete analysis matters more
        # than keeping the page short.
        analysis or "(Analysis not available)",
        "",
    ])


def stamp_generated_frontmatter_dates(content: str, date: str) -> str:
    match = FRONTMATTER_RE.match(content)
    if not match:
        return content
    payload = match.group(2)
    payload = _set_or_append_date(payload, "created", date)
    payload = _set_or_append_date(payload, "updated", date)
    # TS parity: the tail is content.slice(match[0].length) — AFTER the
    # whole match (closing fence included). Slicing from inside group 3
    # duplicates the closer into a second empty frontmatter block.
    return f"{match.group(1)}{payload}{match.group(3)}{content[match.end():]}"


def stamp_generated_log_date(content: str, date: str) -> str:
    normalized = re.sub(r"\bYYYY-MM-DD\b", date, content)
    if re.search(r"^\s*##\s*\[?\d{4}-\d{2}-\d{2}\]?", normalized, re.MULTILINE):
        return re.sub(
            r"^(\s*##\s*\[?)\d{4}-\d{2}-\d{2}(\]?)",
            f"\\g<1>{date}\\g<2>",
            normalized,
            count=1,
            flags=re.MULTILINE,
        )
    return normalized


def _set_or_append_date(payload: str, key: str, date: str) -> str:
    line_re = re.compile(rf"(^|\n)({key}\s*:\s*)[^\n\r]*", re.IGNORECASE)
    if line_re.search(payload):
        return line_re.sub(lambda m: f"{m.group(1)}{m.group(2)}{date}", payload)
    return f"{payload.rstrip()}\n{key}: {date}"


def is_valid_source_reference(source: str, active_source_identity: str) -> bool:
    """Port of isValidSourceReference (ingest.ts:1633+)."""
    from backend.wiki.path_utils import normalize_path
    from backend.wiki.source_identity import source_reference_identity

    normalized = re.sub(r"^(?:\./)+", "", normalize_path(source))
    key = normalized.lower()
    identity_key = normalize_path(active_source_identity).lower()
    if not normalized or normalized.startswith("/") or re.match(r"^[a-z]:/", normalized, re.IGNORECASE):
        return False
    if any(part == ".." for part in normalized.split("/")):
        return False
    if source_reference_identity(normalized).lower() == identity_key:
        return True
    if key in ("wiki/index.md", "wiki/overview.md", "wiki/log.md"):
        return False
    if key == ".llm-wiki" or key.startswith(".llm-wiki/"):
        return False
    return True


def canonicalize_sources_field(content: str, source_identity: str) -> str:
    """Port of canonicalizeSourcesField (ingest.ts:1648+)."""
    from backend.wiki.path_utils import get_file_name, normalize_path
    from backend.wiki.source_identity import source_reference_identity
    from backend.wiki.sources_merge import parse_sources, write_sources

    if not content.startswith("---\n"):
        return content

    identity_key = normalize_path(source_identity).lower()
    identity_base_name = get_file_name(source_identity).lower()
    source_values = parse_sources(content)
    canonical_values = [
        canonical
        for source in source_values
        if is_valid_source_reference(source, source_identity)
        for canonical in [_canonicalize_one(
            source, identity_key, identity_base_name, source_identity
        )]
    ]
    if not any(normalize_path(s).lower() == identity_key for s in canonical_values):
        canonical_values.append(source_identity)

    seen: set[str] = set()
    deduped: list[str] = []
    for source in canonical_values:
        key = normalize_path(source).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(source)

    return write_sources(content, deduped)


def _canonicalize_one(source: str, identity_key: str, identity_base_name: str, source_identity: str) -> str:
    from backend.wiki.source_identity import source_reference_identity

    normalized = source_reference_identity(source)
    key = normalized.lower()
    if key == identity_key:
        return source_identity
    if "/" not in normalized and key == identity_base_name:
        return source_identity
    return normalized


def build_deterministic_ingest_log(source_identity: str, date: str) -> str:
    """Port of buildDeterministicIngestLog (ingest.ts:628-637): the log
    entry appended when the model omitted one."""
    return f"## [{date}] ingest | {source_identity}"
