"""Frontmatter array-field merging ported from llm_wiki src/lib/sources-merge.ts.

parseFrontmatterArray / writeFrontmatterArray / mergeArrayFieldsIntoContent
(36-209) plus the backward-compatible single-field wrappers parseSources /
writeSources / mergeSourcesLists / mergeSourcesIntoContent (219-257).

Unions array fields (`sources`, `tags`, `related`, ...) across re-ingests
so a second source contributing to the same page never clobbers the first
source's metadata. Always emits the inline form `name: ["a", "b"]`.
"""

from __future__ import annotations

import re

# Literal-\n fences, exactly as the TS regexes spell them (CRLF frontmatter
# is intentionally not matched by this module).
_FM_BLOCK_RE = re.compile(r"^---\n([\s\S]*?)\n---")


def parse_frontmatter_array(content: str, field_name: str) -> list[str]:
    """Port of parseFrontmatterArray (sources-merge.ts:36-63).

    Handles inline form (`name: ["a", "b"]` or `name: [a, b]`) and block
    form (`name:\n  - a\n  - b`). Strips quotes (single or double) from
    items. Returns [] for missing field, malformed parse, or no frontmatter.
    """
    fm_match = _FM_BLOCK_RE.match(content)
    if not fm_match:
        return []
    fm = fm_match.group(1)
    escaped = re.escape(field_name)

    block_re = re.compile(
        rf"^{escaped}:\s*\n((?:[ \t]+-\s+.+\n?)+)",
        re.MULTILINE,
    )
    block = block_re.search(fm)
    if block:
        out: list[str] = []
        for line in block.group(1).split("\n"):
            m = re.match(r'^\s+-\s+["\']?(.+?)["\']?\s*$', line)
            if m and m.group(1):
                out.append(m.group(1).strip())
        return out

    inline_re = re.compile(rf"^{escaped}:\s*\[([^\]]*)\]", re.MULTILINE)
    inline = inline_re.search(fm)
    if not inline:
        return []
    body = inline.group(1).strip()
    if body == "":
        return []
    return _split_inline_array(body)


def _split_inline_array(body: str) -> list[str]:
    """Port of splitInlineArray (sources-merge.ts:65-101)."""
    out: list[str] = []
    current = ""
    quote: str | None = None
    escaped = False

    for ch in body:
        if escaped:
            current += ch
            escaped = False
            continue
        if quote == '"' and ch == "\\":
            escaped = True
            continue
        if (ch == '"' or ch == "'") and quote is None:
            quote = ch
            continue
        if quote == ch:
            quote = None
            continue
        if ch == "," and quote is None:
            value = current.strip()
            if value:
                out.append(value)
            current = ""
            continue
        current += ch

    value = current.strip()
    if value:
        out.append(value)
    return out


def write_frontmatter_array(content: str, field_name: str, values: list[str]) -> str:
    """Port of writeFrontmatterArray (sources-merge.ts:114-147).

    Rewrite (or insert) a frontmatter array field, preserving all other
    frontmatter lines and order. Always emits the inline form. Returns
    content unchanged if the input has no frontmatter at all.
    """
    fm_match = re.match(r"^(---\n)([\s\S]*?)(\n---)", content)
    if not fm_match:
        return content

    open_delim, fm_body, close_delim = fm_match.group(1), fm_match.group(2), fm_match.group(3)
    escaped = re.escape(field_name)
    serialized = ", ".join(_quote_inline_array_value(v) for v in values)
    new_line = f"{field_name}: [{serialized}]"

    # Replace inline form in place — preserves field ordering.
    inline_re = re.compile(rf"^{escaped}:\s*\[[^\]]*\]", re.MULTILINE)
    if inline_re.search(fm_body):
        rewritten = inline_re.sub(new_line, fm_body)
        return f"{open_delim}{rewritten}{close_delim}{content[len(fm_match.group(0)):]}"

    # Replace block form in place, normalized to inline form.
    block_re = re.compile(
        rf"^{escaped}:\s*\n((?:[ \t]+-\s+.+\n?)+)",
        re.MULTILINE,
    )
    if block_re.search(fm_body):
        rewritten = block_re.sub(new_line, fm_body)
        return f"{open_delim}{rewritten}{close_delim}{content[len(fm_match.group(0)):]}"

    # Field absent — append at end of frontmatter.
    rewritten = f"{fm_body}\n{new_line}"
    return f"{open_delim}{rewritten}{close_delim}{content[len(fm_match.group(0)):]}"


def _quote_inline_array_value(value: str) -> str:
    """Port of quoteInlineArrayValue (sources-merge.ts:149-151)."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def merge_lists(existing: list[str], incoming: list[str]) -> list[str]:
    """Port of mergeLists (sources-merge.ts:158-171).

    Union-merge two array values. Case-insensitive dedup. First-seen
    casing wins (keeps users' original filename casing stable).
    """
    seen: set[str] = set()
    out: list[str] = []
    for s in [*existing, *incoming]:
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def merge_array_fields_into_content(
    new_content: str,
    existing_content: str | None,
    fields: list[str],
) -> str:
    """Port of mergeArrayFieldsIntoContent (sources-merge.ts:184-209).

    For each requested field, union the existing-on-disk value with the
    incoming new value, and rewrite the new content's frontmatter.

    Fast-paths: existing null/empty -> newContent verbatim; existing has
    no frontmatter -> newContent verbatim; no field actually changes ->
    newContent verbatim (stable reference).
    """
    if not existing_content:
        return new_content
    if not re.match(r"^---\n", existing_content):
        return new_content

    result = new_content
    changed = False
    for field in fields:
        old_values = parse_frontmatter_array(existing_content, field)
        if not old_values:
            continue  # field absent in existing -> nothing to preserve
        new_values = parse_frontmatter_array(result, field)
        merged = merge_lists(old_values, new_values)
        if len(merged) == len(new_values) and all(a == b for a, b in zip(merged, new_values)):
            continue  # no-op for this field
        result = write_frontmatter_array(result, field, merged)
        changed = True
    return result if changed else new_content


# --- Backward-compatible single-field exports (sources-merge.ts:219-257) ---


def parse_sources(content: str) -> list[str]:
    """Extract `sources: [...]` from a wiki page's frontmatter."""
    return parse_frontmatter_array(content, "sources")


def write_sources(content: str, sources: list[str]) -> str:
    """Rewrite the `sources:` field."""
    return write_frontmatter_array(content, "sources", sources)


def merge_sources_lists(existing: list[str], incoming: list[str]) -> list[str]:
    """Merge two source lists (case-insensitive dedup, first-seen casing wins)."""
    return merge_lists(existing, incoming)


def merge_sources_into_content(new_content: str, existing_content: str | None) -> str:
    """Sources-only convenience wrapper — equivalent to
    mergeArrayFieldsIntoContent(new_content, existing_content, ["sources"])."""
    return merge_array_fields_into_content(new_content, existing_content, ["sources"])
