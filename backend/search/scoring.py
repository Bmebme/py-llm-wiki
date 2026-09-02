"""Per-file scoring + snippet/title/image helpers — port of search.rs
score_file (814-879), build_snippet (1600-1624), extract_title
(1004-1032), extract_image_refs (1033-1056)."""

from __future__ import annotations

from pathlib import Path

from backend.search.tokenize import (
    count_occurrences,
    token_match_score,
)

FILENAME_EXACT_BONUS = 200.0
PHRASE_IN_TITLE_BONUS = 50.0
PHRASE_IN_CONTENT_PER_OCC = 20.0
MAX_PHRASE_OCC_COUNTED = 10
TITLE_TOKEN_WEIGHT = 5.0
CONTENT_TOKEN_WEIGHT = 1.0
SNIPPET_CONTEXT = 80


def extract_title(content: str, file_name: str) -> str:
    """Port of extract_title: frontmatter `title:` wins; otherwise the
    first markdown heading; otherwise the file stem."""
    has_frontmatter = content.startswith("---")
    in_frontmatter = has_frontmatter
    frontmatter_closed = False
    lines = content.split("\n")
    for line in lines[1:] if has_frontmatter else lines:
        trimmed = line.strip()
        if in_frontmatter and trimmed == "---":
            in_frontmatter = False
            frontmatter_closed = True
            continue
        if in_frontmatter and trimmed.startswith("title:"):
            return trimmed[len("title:"):].strip().strip('"').strip("'")
        if has_frontmatter and not frontmatter_closed:
            continue
        if trimmed.startswith("#"):
            return trimmed.lstrip("#").strip()
        break
    stem = Path(file_name).stem
    return stem if stem else file_name


def extract_image_refs(content: str) -> list[dict]:
    """Port of extract_image_refs: markdown image urls, deduped in
    document order."""
    out: list[dict] = []
    seen: set[str] = set()
    rest = content
    while True:
        start = rest.find("![")
        if start < 0:
            break
        rest = rest[start + 2:]
        alt_end = rest.find("](")
        if alt_end < 0:
            break
        alt = rest[:alt_end]
        rest = rest[alt_end + 2:]
        url_end = rest.find(")")
        if url_end < 0:
            break
        url = rest[:url_end]
        if url.strip() and not any(ch.isspace() for ch in url) and url not in seen:
            seen.add(url)
            out.append({"url": url, "alt": alt})
        rest = rest[url_end + 1:]
    return out


def build_snippet(content: str, query: str) -> str:
    """Port of build_snippet: ~80 chars of context around the first
    match, newlines collapsed, ellipses on the cut edges."""
    lower = content.lower()
    q = query.lower()
    idx = lower.find(q) if q else -1
    if idx < 0:
        idx = 0
    # Map byte offset → char index (Rust walks char_indices).
    match_char = 0
    for i, ch in enumerate(content):
        if len(content[:i].encode("utf-8")) >= idx:
            match_char = i
            break
    query_chars = max(1, len(query))
    start_char = max(0, match_char - SNIPPET_CONTEXT)
    end_char = min(len(content), match_char + query_chars + SNIPPET_CONTEXT)
    snippet = content[start_char:end_char].replace("\n", " ")
    if start_char > 0:
        snippet = f"...{snippet}"
    if end_char < len(content):
        snippet += "..."
    return snippet


def score_file(
    file_name: str,
    content: str,
    tokens: list[str],
    query_phrase: str,
    query: str,
    include_content: bool = False,
) -> dict | None:
    """Port of score_file. Returns a result dict (path/title filled in
    by the caller) or None when nothing matches."""
    title = extract_title(content, file_name)
    title_text = f"{title} {file_name}"
    title_lower = title_text.lower()
    content_lower = content.lower()
    stem = file_name.removesuffix(".md").lower()

    filename_exact = bool(query_phrase) and stem == query_phrase
    title_has_phrase = bool(query_phrase) and query_phrase in title_lower
    content_phrase_occ = min(
        count_occurrences(content_lower, query_phrase), MAX_PHRASE_OCC_COUNTED
    )
    title_token_score = token_match_score(title_text, tokens)
    content_token_score = token_match_score(content, tokens)

    if not (
        filename_exact
        or title_has_phrase
        or content_phrase_occ
        or title_token_score
        or content_token_score
    ):
        return None

    score = (
        (FILENAME_EXACT_BONUS if filename_exact else 0.0)
        + (PHRASE_IN_TITLE_BONUS if title_has_phrase else 0.0)
        + content_phrase_occ * PHRASE_IN_CONTENT_PER_OCC
        + title_token_score * TITLE_TOKEN_WEIGHT
        + content_token_score * CONTENT_TOKEN_WEIGHT
    )

    snippet_anchor = (
        query_phrase
        if content_phrase_occ > 0
        else next((t for t in tokens if t in content_lower), query)
    )

    result = {
        "title": title,
        "snippet": build_snippet(content, snippet_anchor),
        "title_match": title_token_score > 0 or title_has_phrase,
        "score": score,
        "images": extract_image_refs(content),
    }
    if include_content:
        result["content"] = content
    return result
