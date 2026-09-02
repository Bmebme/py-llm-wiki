"""Clean up an LLM-generated wiki page body before it hits disk.

Full port of llm_wiki src/lib/ingest-sanitize.ts (sanitizeIngestedFileContent).

Recurring shapes the model emits:

  1. The whole page wrapped in a ```yaml / ```md / ```markdown / bare
     ``` code fence.
  2. A leading `frontmatter:` key that turns the document into a
     malformed nested-yaml shape.
  3. A frontmatter payload whose opening `---` is missing but whose
     closing `---` is present (the model starts "inside" the YAML block).
  4. Inline wikilink lists without the outer brackets, e.g.
     `related: [[a]], [[b]], [[c]]` — not valid YAML flow syntax.

Each pattern is anchored at the very start of the document (or at
top-level frontmatter scope), so legitimate fenced code blocks deep in
the body or a `frontmatter:` mention inside prose are left alone.
"""

from __future__ import annotations

import re

_OPEN_FENCE_RE = re.compile(
    r"^(?:﻿)?(?:[ \t]*\r?\n)*[ \t]*```(?:yaml|md|markdown)?[ \t]*\r?\n",
    re.IGNORECASE,
)
_CLOSE_FENCE_RE = re.compile(r"\r?\n[ \t]*```[ \t]*\r?\n?\s*$")
_FRONTMATTER_ONLY_FENCE_RE = re.compile(
    r"^(---[ \t]*\r?\n[\s\S]*?^---[ \t]*\r?\n)[ \t]*```[ \t]*(?:\r?\n|$)",
    re.MULTILINE,
)
_FRONTMATTER_KEY_PREFIX_RE = re.compile(r"^[ \t]*frontmatter\s*:\s*\r?\n(?=[ \t]*---\s*\r?\n)")
_OPENING_FENCE_PRESENT_RE = re.compile(r"^[ \t]*---\s*(\r?\n|$)")
_METADATA_FIELD_RE = re.compile(r"^(type|title|created|updated|tags|related|sources)\s*:", re.IGNORECASE)
_HEADING_RE = re.compile(r"^#{1,6}\s+")
_FM_BLOCK_RE = re.compile(r"^(---[ \t]*(\r?\n))([\s\S]*?)(\r?\n---[ \t]*(?:\r?\n|$))")
_WIKILINK_LIST_LINE_RE = re.compile(
    r"^(\s*[A-Za-z_][\w-]*\s*:\s*)(\[\[[^\]]+\]\](?:\s*,\s*\[\[[^\]]+\]\])+)\s*$"
)


def _collapse_double_frontmatter_fence(content: str) -> str:
    """Real-world LLM wrinkle (seen with deepseek-chat): the model emits
    a second empty frontmatter block right after the first close:

        ---
        type: source
        ---
        <blank>
        ---
        # Body

    The read-time parser tolerates it, but the canonical file on disk
    should carry a single fence. Collapse the adjacent bare `---` into
    the first block's closer.
    """
    # The FILE block content may start with a newline (the LLM puts the
    # frontmatter on the line after the ---FILE: opener) — tolerate
    # leading whitespace before the first fence and preserve it.
    match = re.match(
        r"^[ \t\r\n]*(---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*\r?\n)(?:[ \t]*\r?\n)*---[ \t]*\r?\n",
        content,
    )
    if match:
        return content[: match.start(1)] + match.group(1) + content[match.end():]
    return content


def sanitize_ingested_file_content(content: str) -> str:
    """Port of sanitizeIngestedFileContent (ingest-sanitize.ts:58-89)."""
    cleaned = content

    # (1) Strip a code fence wrapping the whole document or just its
    # frontmatter block.
    cleaned = _strip_outer_code_fence(cleaned)

    # (2) Strip a stray `frontmatter:` line that prefixes the real
    # `---` block.
    cleaned = _strip_frontmatter_key_prefix(cleaned)

    # (2.5) Repair a missing opening frontmatter fence when the model
    # clearly emitted frontmatter lines followed by a closing fence.
    cleaned = _add_missing_opening_frontmatter_fence(cleaned)

    # (3) Repair `key: [[a]], [[b]], [[c]]` lines inside the
    # frontmatter block so they're valid YAML. Body wikilinks are left
    # alone.
    cleaned = _repair_wikilink_lists_in_frontmatter(cleaned)

    # (4) Collapse a second empty frontmatter block immediately after
    # the first (real-world LLM output wrinkle).
    cleaned = _collapse_double_frontmatter_fence(cleaned)

    return cleaned


def _strip_outer_code_fence(content: str) -> str:
    """Top-level fence wrapper: removes the open + matching close fence
    lines (ingest-sanitize.ts:92-112).

    Only acts when the FIRST non-empty line is an opening fence with a
    matching close either at the end of the document or directly after a
    complete frontmatter block — pages that legitimately start with an
    unclosed fence are never touched.
    """
    open_match = _OPEN_FENCE_RE.match(content)
    if not open_match:
        return content
    after_open = content[len(open_match.group(0)):]

    # Closing fence: a final ``` on its own line, ignoring trailing
    # whitespace/newlines after it.
    close = _CLOSE_FENCE_RE.search(after_open)
    if close:
        return after_open[: close.start()]

    # Some models close the fence immediately after the frontmatter and
    # continue with an unfenced Markdown body. Only strip this shape when
    # the fenced section is exactly a complete `---` frontmatter block.
    frontmatter_only = _FRONTMATTER_ONLY_FENCE_RE.search(after_open)
    if not frontmatter_only:
        return content
    return frontmatter_only.group(1) + after_open[len(frontmatter_only.group(0)):]


def _strip_frontmatter_key_prefix(content: str) -> str:
    """Strip a leading `frontmatter:` line followed by the real
    frontmatter block (ingest-sanitize.ts:120-124). Only acts when the
    next non-empty line is `---`, so prose mentioning "frontmatter:" is
    unaffected.
    """
    m = _FRONTMATTER_KEY_PREFIX_RE.match(content)
    if not m:
        return content
    return content[len(m.group(0)):]


def _add_missing_opening_frontmatter_fence(content: str) -> str:
    """Port of addMissingOpeningFrontmatterFence (ingest-sanitize.ts:126-148)."""
    if _OPENING_FENCE_PRESENT_RE.match(content):
        return content

    lines = re.split(r"\r?\n", content)
    first_content_idx = next((i for i, line in enumerate(lines) if line.strip()), -1)
    if first_content_idx < 0:
        return content

    first = lines[first_content_idx].strip()
    if not _METADATA_FIELD_RE.match(first):
        return content

    search_end = min(len(lines), first_content_idx + 30)
    for i in range(first_content_idx + 1, search_end):
        trimmed = lines[i].strip()
        if trimmed == "---":
            return f"---\n{'\n'.join(lines[first_content_idx:])}"
        if _HEADING_RE.match(trimmed):
            break

    return content


def _repair_wikilink_lists_in_frontmatter(content: str) -> str:
    """Inside the frontmatter block (between the opening `---` and the
    closing `---`), rewrite invalid wikilink-list lines. Lines outside
    the frontmatter block are left untouched (ingest-sanitize.ts:155-181).
    """
    m = _FM_BLOCK_RE.match(content)
    if not m:
        return content

    repaired_payload = []
    for line in re.split(r"\r?\n", m.group(3)):
        lm = _WIKILINK_LIST_LINE_RE.match(line)
        if not lm:
            repaired_payload.append(line)
            continue
        items = [f'"{s.strip()}"' for s in lm.group(2).split(",") if s.strip()]
        repaired_payload.append(f"{lm.group(1)}[{', '.join(items)}]")

    # Rebuild from captured delimiters instead of assuming the opening
    # fence is four bytes — CRLF makes `---\r\n` five bytes.
    return (
        m.group(1)
        + m.group(2).join(repaired_payload)
        + m.group(4)
        + content[len(m.group(0)):]
    )
