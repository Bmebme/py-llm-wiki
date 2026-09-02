"""FILE/REVIEW block parsing — port of llm_wiki src/lib/ingest.ts.

The stage-2 LLM output is a tagged-block grammar, not JSON:
    ---FILE: wiki/path/to/page.md---
    (complete file content)
    ---END FILE---
    ---REVIEW: type | Title---
    OPTIONS: Create Page | Skip
    PAGES: wiki/page1.md, wiki/page2.md
    SEARCH: query 1 | query 2
    ---END REVIEW---

This module ports parseFileBlocks (including the H1/H3/H5/H6 hazard
fixes), isSafeIngestPath (path-traversal gate at the parse boundary),
and parseReviewBlocks. Behavior is byte-compatible; the original test
fixtures in src/lib/ingest-parse.test.ts are the spec.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Legacy regex kept for parity with the exported TS constant.
FILE_BLOCK_REGEX = re.compile(r"---FILE:\s*([^\n]+?)\s*---\n([\s\S]*?)---END FILE---")

# Line-level openers / closers. Case-insensitive, tolerant of interior
# whitespace, anchored to the whole trimmed line (ingest.ts OPENER_LINE).
OPENER_LINE = re.compile(r"^---\s*FILE:\s*(.+?)\s*---\s*$", re.IGNORECASE)
CLOSER_LINE = re.compile(r"^---\s*END\s+FILE\s*---\s*$", re.IGNORECASE)

# CommonMark fences: triple+ backticks or tildes, ≤3 spaces indent.
FENCE_LINE = re.compile(r"^\s{0,3}(```+|~~~+)")

REVIEW_BLOCK_REGEX = re.compile(
    r"---REVIEW:\s*(\w[\w-]*)\s*\|\s*(.+?)\s*---\n([\s\S]*?)---END REVIEW---"
)

REVIEW_TYPES = ("contradiction", "duplicate", "missing-page", "suggestion")


@dataclass
class ParsedFileBlock:
    path: str
    content: str


@dataclass
class ParseFileBlocksResult:
    blocks: list[ParsedFileBlock]
    warnings: list[str] = field(default_factory=list)
    truncated_paths: list[str] = field(default_factory=list)


_WINDOWS_INVALID_CHARS = re.compile(r'[<>:"|?*]')
_END_SPACE_OR_DOT = re.compile(r"[ .]$")
_WINDOWS_DEVICE_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def is_safe_ingest_path(path: str) -> bool:
    """Port of isSafeIngestPath (ingest.ts). Rejects anything that could
    escape the project's wiki/ directory from LLM-generated text."""
    if not isinstance(path, str) or len(path.strip()) == 0:
        return False
    # No control / NUL bytes anywhere.
    if re.search(r"[\x00-\x1f]", path):
        return False
    # Reject absolute paths (POSIX) and Windows drive letters / UNC.
    if path.startswith("/") or path.startswith("\\"):
        return False
    if re.match(r"^[a-zA-Z]:", path):
        return False
    # Normalize backslashes so Windows-style payloads can't sneak past.
    normalized = path.replace("\\", "/")
    segments = normalized.split("/")
    if any(seg == ".." for seg in segments):
        return False
    if any(not _is_windows_safe_segment(seg) for seg in segments):
        return False
    # Must live under wiki/ — the only tree the ingest pipeline writes to.
    if not normalized.startswith("wiki/"):
        return False
    return True


def _is_windows_safe_segment(segment: str) -> bool:
    if len(segment) == 0:
        return False
    if _WINDOWS_INVALID_CHARS.search(segment):
        return False
    if _END_SPACE_OR_DOT.search(segment):
        return False
    stem = segment.split(".")[0].upper()
    if not stem:
        return False
    return stem not in _WINDOWS_DEVICE_NAMES


def parse_file_blocks(text: str) -> ParseFileBlocksResult:
    """Port of parseFileBlocks (ingest.ts:456-557).

    Hazard fixes: H1 CRLF normalization, H3 marker whitespace/case
    tolerance, H5 fenced-code awareness for the closer, H6 empty-path
    surfacing. H2 (stream truncation) surfaces as warning + truncated
    path instead of a silent drop.
    """
    normalized = text.replace("\r\n", "\n")
    lines = normalized.split("\n")

    blocks: list[ParsedFileBlock] = []
    warnings: list[str] = []
    truncated_paths: list[str] = []

    i = 0
    while i < len(lines):
        opener_match = OPENER_LINE.match(lines[i])
        if opener_match is None:
            i += 1
            continue
        path = opener_match.group(1).strip()
        i += 1  # consume opener

        content_lines: list[str] = []
        fence_marker: str | None = None  # '`' or '~'
        fence_len = 0
        closed = False

        while i < len(lines):
            line = lines[i]

            # H5 fix: update fence state BEFORE checking the closer.
            # CommonMark rule: close only on same char, len >= opening.
            fence_match = FENCE_LINE.match(line)
            if fence_match:
                run = fence_match.group(1)
                char = run[0]
                length = len(run)
                if fence_marker is None:
                    fence_marker = char
                    fence_len = length
                elif char == fence_marker and length >= fence_len:
                    fence_marker = None
                    fence_len = 0
                content_lines.append(line)
                i += 1
                continue

            # A closer only counts outside any code fence.
            if fence_marker is None and CLOSER_LINE.match(line):
                closed = True
                i += 1
                break

            content_lines.append(line)
            i += 1

        if not closed:
            # H2: truncation — surface the drop instead of hiding it.
            path_label = path or "(unnamed)"
            msg = (
                f'FILE block "{path_label}" was not closed before end of stream — '
                "likely truncation (model hit max_tokens, timeout, or connection "
                "dropped). Block dropped."
            )
            warnings.append(msg)
            if is_safe_ingest_path(path):
                truncated_paths.append(path)
            continue

        if not path:
            msg = "FILE block with empty path skipped (LLM omitted the path after `---FILE:`)."
            warnings.append(msg)
            continue

        if not is_safe_ingest_path(path):
            msg = (
                f'FILE block with unsafe path "{path}" rejected (must be under '
                "wiki/, no .., no absolute paths, and Windows-safe file names)."
            )
            warnings.append(msg)
            continue

        blocks.append(ParsedFileBlock(path=path, content="\n".join(content_lines)))

    return ParseFileBlocksResult(
        blocks=blocks, warnings=warnings, truncated_paths=truncated_paths
    )


def parse_review_blocks(text: str, source_path: str) -> list[dict]:
    """Port of parseReviewBlocks (ingest.ts:2071-2132). Returns ReviewItem
    dicts without id/resolved/createdAt (the review store adds those)."""
    items: list[dict] = []
    for match in REVIEW_BLOCK_REGEX.finditer(text):
        raw_type = match.group(1).strip().lower()
        title = match.group(2).strip()
        body = match.group(3).strip()

        review_type = raw_type if raw_type in REVIEW_TYPES else "confirm"

        options_match = re.search(r"^OPTIONS:\s*(.+)$", body, re.MULTILINE)
        options = (
            [{"label": o.strip(), "action": o.strip()}
             for o in options_match.group(1).split("|")]
            if options_match
            else [
                {"label": "Approve", "action": "Approve"},
                {"label": "Skip", "action": "Skip"},
            ]
        )

        pages_match = re.search(r"^PAGES:\s*(.+)$", body, re.MULTILINE)
        affected_pages = (
            [p.strip() for p in pages_match.group(1).split(",")]
            if pages_match
            else None
        )

        search_match = re.search(r"^SEARCH:\s*(.+)$", body, re.MULTILINE)
        search_queries = (
            [q.strip() for q in search_match.group(1).split("|") if q.strip()]
            if search_match
            else None
        )

        # Description = body minus OPTIONS / PAGES / SEARCH lines.
        description = re.sub(r"^OPTIONS:.*$", "", body, flags=re.MULTILINE)
        description = re.sub(r"^PAGES:.*$", "", description, flags=re.MULTILINE)
        description = re.sub(r"^SEARCH:.*$", "", description, flags=re.MULTILINE)
        description = description.strip()

        items.append({
            "type": review_type,
            "title": title,
            "description": description,
            "sourcePath": source_path,
            "affectedPages": affected_pages,
            "searchQueries": search_queries,
            "options": options,
        })
    return items


def count_file_blocks(text: str) -> int:
    return len(re.findall(r"---FILE:\s*[^-]+---", text))
