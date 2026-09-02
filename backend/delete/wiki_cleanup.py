"""String-level wiki cleanup helpers — port of llm_wiki
src/lib/wiki-cleanup.ts. Structural wikilink parsing + normalized keys
(no fuzzy substring matching — deleting `ai.md` must not touch
`[[OpenAI]]`)."""

from __future__ import annotations

import re

WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]")
INDEX_ENTRY_RE = re.compile(r"^\s*[-*]\s*\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]")


def normalize_wiki_ref_key(value: str) -> str:
    """Port of normalizeWikiRefKey: strip path prefixes + .md, collapse
    case and space/hyphen/underscore boundaries."""
    normalized = value.strip().replace("\\", "/")
    leaf = normalized.split("/")[-1] if normalized.split("/") else normalized
    without_md = leaf[:-3] if leaf.lower().endswith(".md") else leaf
    return re.sub(r"[\s\-_]+", "", without_md.lower())


def build_deleted_keys(infos: list[dict]) -> set[str]:
    keys: set[str] = set()
    for info in infos:
        if info.get("slug"):
            keys.add(normalize_wiki_ref_key(info["slug"]))
        if info.get("title"):
            keys.add(normalize_wiki_ref_key(info["title"]))
    return keys


def extract_frontmatter_title(content: str) -> str:
    match = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', content, re.MULTILINE)
    return match.group(1).strip() if match else ""


def clean_index_listing(text: str, deleted_keys: set[str]) -> str:
    """Drop index list-item lines whose primary wikilink targets a
    deleted page. Everything else preserved verbatim."""
    if not deleted_keys:
        return text
    lines = []
    for line in text.split("\n"):
        match = INDEX_ENTRY_RE.match(line)
        if match and normalize_wiki_ref_key(match.group(1).strip()) in deleted_keys:
            continue
        lines.append(line)
    return "\n".join(lines)


def strip_deleted_wikilinks(text: str, deleted_keys: set[str]) -> str:
    """[[deleted]] → deleted; [[deleted|display]] → display; survivors
    untouched."""
    if not deleted_keys:
        return text

    def replace(match: re.Match) -> str:
        target = match.group(1)
        display = match.group(2)
        if normalize_wiki_ref_key(target.strip()) in deleted_keys:
            return display if display is not None else target
        return match.group(0)

    return WIKILINK_RE.sub(replace, text)
