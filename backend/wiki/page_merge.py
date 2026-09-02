"""Wiki page merge layer ported from llm_wiki src/lib/page-merge.ts.

This module ports the DETERMINISTIC layer of mergePageContent only:

  merge_page_content_deterministic(incoming, existing, source_file_name,
                                   replace_existing_body, today)

which implements every branch that never calls the model:

  (a) brand-new page / byte-identical re-ingest fast paths;
  (b) union-merge of the array frontmatter fields
      (sources / tags / related) — zero-cost, always on;
  (c) replaceExistingBody: incoming body wins, but locked scalar fields
      (type / title / created) are forced back to the existing values and
      `updated` is stamped with today's date;
  (d) otherwise: incoming body + union arrays (the TS fallback contract:
      pre-LLM-merge behavior on any merger failure).

TODO(LLM seam): the production TS layer (page-merge.ts:139-201) asks an
injected merger (LLM call) to produce a unified body when old and new
bodies differ, sanity-checks the output (must parse as a frontmatter-bearing
page; body length must stay >= 70% of the longer input), then applies
deterministic post-processing: locked fields forced back, arrays re-unioned
against BOTH sides, `updated` stamped today, and
strip_body_wikilink_path_prefixes() applied to the final body. When that
seam is wired here, merge_page_content_deterministic is the fallback it
returns on failure.

Also ported here (pure deterministic helpers used by that post-processing):
set_frontmatter_scalar, strip_body_wikilink_path_prefixes and the
wikilink-normalization pipeline (page-merge.ts:212-315).
"""

from __future__ import annotations

import re

from backend.wiki.frontmatter import parse_frontmatter
from backend.wiki.sources_merge import merge_array_fields_into_content

# Frontmatter array fields unioned across re-ingests (page-merge.ts:30).
UNION_FIELDS = ["sources", "tags", "related"]

# Frontmatter scalar fields whose existing value MUST survive an ingest
# even if incoming content carries a different value (page-merge.ts:44):
#   - type: changing it would re-categorize the page across folders
#   - title: wikilinks resolve via [[slug]] but display this field
#   - created: a one-time stamp
LOCKED_FIELDS = ["type", "title", "created"]

# Body length safety threshold for the future LLM seam (page-merge.ts:53).
BODY_SHRINK_THRESHOLD = 0.7


def merge_page_content_deterministic(
    incoming: str,
    existing: str | None,
    source_file_name: str,
    replace_existing_body: bool,
    today: str,
) -> str:
    """Deterministic layer of mergePageContent (page-merge.ts:94-201).

    ``source_file_name`` is unused by the deterministic layer today — the
    TS original passes it to the LLM merger and uses it in log messages —
    and is kept in the signature so the future seam can forward it.

    ``today`` replaces the injectable ``today()`` date provider: stamp
    value for the ``updated`` field (format "YYYY-MM-DD").
    """
    del source_file_name  # reserved for the LLM-merge seam

    # Fast path 1: brand-new page.
    if not existing:
        return incoming

    # Fast path 2: byte-identical — re-ingest of the same source file
    # with no actual change.
    if incoming == existing:
        return existing

    # Step 1 — always-on: union the array frontmatter fields.
    array_merged = merge_array_fields_into_content(
        incoming,
        existing,
        list(UNION_FIELDS),
    )

    if replace_existing_body:
        # Corrected single-source replacement (page-merge.ts:119-133):
        # incoming body wins, locked metadata forced back, updated stamped.
        old_parsed = parse_frontmatter(existing)
        replacement = array_merged
        for field in LOCKED_FIELDS:
            existing_value = (old_parsed.frontmatter or {}).get(field)
            if isinstance(existing_value, str) and existing_value != "":
                replacement = set_frontmatter_scalar(replacement, field, existing_value)
        return set_frontmatter_scalar(replacement, "updated", today)

    # Fast path 3: bodies identical (only frontmatter array fields
    # differed) — the array merge already produced the right output
    # (page-merge.ts:134-136). Otherwise the TS layer invokes the LLM
    # merger here; on any failure or sanity-check rejection it returns
    # exactly this array-merged value (page-merge.ts:140-178).
    # TODO(LLM seam): insert the body-merge call (and its sanity checks)
    # here — see the module docstring.
    return array_merged


def set_frontmatter_scalar(content: str, field_name: str, value: str) -> str:
    """Port of setFrontmatterScalar (page-merge.ts:350-371).

    Set a scalar frontmatter field to `value` in the inline form
    `field: value`. If the field already exists (scalar form only — no
    `[`, no block list), the line is replaced in place; otherwise the
    field is appended at the end of the frontmatter block. Returns
    content unchanged if it has no frontmatter at all (or CRLF fences —
    the TS regexes are literal-\\n by design).
    """
    fm_match = re.match(r"^(---\n)([\s\S]*?)(\n---)", content)
    if not fm_match:
        return content
    open_delim, fm_body, close_delim = fm_match.group(1), fm_match.group(2), fm_match.group(3)
    escaped = re.escape(field_name)
    new_line = f"{field_name}: {value}"

    # Only match scalar form (no `[`, no `\n  -`).
    line_re = re.compile(rf"^{escaped}:\s*(?!\[)([^\n]*)", re.MULTILINE)
    if line_re.search(fm_body):
        rewritten = line_re.sub(new_line, fm_body)
        return f"{open_delim}{rewritten}{close_delim}{content[len(fm_match.group(0)):]}"
    # Field absent — append.
    rewritten = f"{fm_body}\n{new_line}"
    return f"{open_delim}{rewritten}{close_delim}{content[len(fm_match.group(0)):]}"


# ─── Wikilink path-prefix normalization (page-merge.ts:212-315) ──────
#
# Normalize page links after an LLM merge without touching frontmatter,
# prose paths, code examples, or Obsidian image/file embeds. Project
# schemas route pages into directories on disk, but cross-page links use
# bare slugs, so `[[clients/foo-overview]]` is repaired to
# `[[foo-overview]]` deterministically.

_FRONTMATTER_STRIP_RE = re.compile(r"^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)")
_FENCE_MARKER_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_PAGE_WIKILINK_RE = re.compile(r"\[\[([^\]|\n]+)(?:\|([^\]\n]*))?\]\]")
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*:", re.IGNORECASE)


def strip_body_wikilink_path_prefixes(content: str) -> str:
    """Port of stripBodyWikilinkPathPrefixes (page-merge.ts:212-222)."""
    frontmatter = _FRONTMATTER_STRIP_RE.match(content)
    if not frontmatter:
        return content

    body = content[len(frontmatter.group(0)):]
    if "[[" not in body:
        return content

    return f"{frontmatter.group(0)}{_normalize_wikilinks_outside_code(body)}"


def _normalize_wikilinks_outside_code(body: str) -> str:
    """Port of normalizeWikilinksOutsideCode (page-merge.ts:224-247).

    Each line is matched with its trailing newline preserved: the stripped
    text is used for fence/marker checks, but the replacement keeps the
    original line (including CRLF endings) intact.
    """
    fence: dict | None = None
    out: list[str] = []

    for line in body.splitlines(keepends=True):
        content = re.sub(r"\r?\n$", "", line)
        marker_match = _FENCE_MARKER_RE.match(content)
        if marker_match:
            marker = marker_match.group(1)[0]
            length = len(marker_match.group(1))
            if fence is None:
                fence = {"marker": marker, "length": length}
            elif (
                marker == fence["marker"]
                and length >= fence["length"]
                and content[len(marker_match.group(0)):].strip() == ""
            ):
                fence = None
            out.append(line)
            continue
        if fence is not None or re.match(r"^(?: {4}|\t)", content):
            out.append(line)
            continue
        out.append(_replace_outside_inline_code(line))

    return "".join(out)


def _replace_outside_inline_code(text: str) -> str:
    """Port of replaceOutsideInlineCode (page-merge.ts:249-274)."""
    output: list[str] = []
    cursor = 0
    while cursor < len(text):
        opening = text.find("`", cursor)
        if opening < 0:
            output.append(_replace_wikilink_prefixes(text[cursor:]))
            return "".join(output)

        output.append(_replace_wikilink_prefixes(text[cursor:opening]))
        run_end = opening + 1
        while run_end < len(text) and text[run_end] == "`":
            run_end += 1
        delimiter = text[opening:run_end]
        closing = text.find(delimiter, run_end)
        if closing < 0:
            # An unmatched run is ordinary Markdown text, not an inline
            # code span.
            output.append(_replace_wikilink_prefixes(text[opening:run_end]))
            cursor = run_end
            continue

        output.append(text[opening:closing + len(delimiter)])
        cursor = closing + len(delimiter)

    return "".join(output)


def _replace_wikilink_prefixes(text: str) -> str:
    """Port of replaceWikilinkPrefixes (page-merge.ts:278-294)."""
    out: list[str] = []
    last = 0
    for m in _PAGE_WIKILINK_RE.finditer(text):
        raw_target = m.group(1)
        raw_alias = m.group(2)
        offset = m.start()
        out.append(text[last:offset])

        if offset > 0 and text[offset - 1] == "!":
            out.append(m.group(0))
            last = m.end()
            continue
        preceding = re.search(r"\\*$", text[:offset])
        if preceding and len(preceding.group(0)) % 2 == 1:
            out.append(m.group(0))
            last = m.end()
            continue

        target = raw_target.strip()
        normalized_target = _bare_wikilink_target(target)
        if normalized_target == target:
            out.append(m.group(0))
            last = m.end()
            continue

        alias = "" if raw_alias is None else f"|{raw_alias}"
        out.append(f"[[{normalized_target}{alias}]]")
        last = m.end()

    out.append(text[last:])
    return "".join(out)


def _bare_wikilink_target(target: str) -> str:
    """Port of bareWikilinkTarget (page-merge.ts:296-315)."""
    if not target or target.startswith("#"):
        return target
    # URI-like targets are not wiki page paths.
    if _SCHEME_RE.match(target):
        return target

    fragment_index = target.find("#")
    page_target = target[:fragment_index] if fragment_index >= 0 else target
    fragment = target[fragment_index:] if fragment_index >= 0 else ""
    normalized_path = page_target.replace("\\", "/")
    if "/" not in normalized_path:
        return target

    leaf = normalized_path.rsplit("/", 1)[-1]
    if not leaf:
        return target
    extension_index = leaf.rfind(".")
    if extension_index > 0 and leaf[extension_index:].lower() != ".md":
        return target

    return f"{leaf}{fragment}"
