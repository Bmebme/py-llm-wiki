"""Query tokenization — port of search.rs tokenize_query (881-913),
is_query_separator (915-936), is_stop_word (938-983)."""

from __future__ import annotations

STOP_WORDS = {
    # Chinese
    "的", "是", "了", "什么", "在", "有", "和", "与", "对", "从",
    # English
    "the", "is", "a", "an", "what", "how", "are", "was", "were",
    "do", "does", "did", "be", "been", "being", "have", "has", "had",
    "it", "its", "in", "on", "at", "to", "for", "of", "with", "by",
    "this", "that", "these", "those",
}

CJK_SEPARATORS = "，。！？、；：“”‘’（）·～…"

# Rust char::is_ascii_punctuation — the exact ASCII punctuation set
# (includes underscore; the TS frontend split regex also lists _ and -).
ASCII_PUNCTUATION = set('!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~')

_EXTRA_SEPARATOR_SET = set(CJK_SEPARATORS)


def is_query_separator(ch: str) -> bool:
    return ch.isspace() or ch in ASCII_PUNCTUATION or ch in _EXTRA_SEPARATOR_SET


def is_stop_word(token: str) -> bool:
    return token in STOP_WORDS


def tokenize_query(query: str) -> list[str]:
    """Port of tokenize_query: lowercase → split on separators → drop
    tokens ≤1 char and stop words → CJK bigrams + single non-stop chars
    + full token for CJK tokens longer than 2 chars → dedup (order
    stable, BTreeSet = sorted by Rust's Ord; Python set preserves
    insertion order — sort for deterministic parity)."""
    raw: list[str] = []
    current: list[str] = []
    for ch in query.lower():
        if is_query_separator(ch):
            if current:
                raw.append("".join(current))
                current = []
        else:
            current.append(ch)
    if current:
        raw.append("".join(current))

    raw = [t for t in raw if len(t) > 1 and not is_stop_word(t)]

    out: list[str] = []
    for token in raw:
        has_cjk = any("㐀" <= ch <= "鿿" for ch in token)
        if has_cjk and len(token) > 2:
            out.extend(token[i:i + 2] for i in range(len(token) - 1))
            out.extend(ch for ch in token if not is_stop_word(ch))
            out.append(token)
        else:
            out.append(token)
    return sorted(set(out))


def trim_query_punctuation(value: str) -> str:
    """Port of trim_query_punctuation (search.rs:985-987): trim separator
    chars from both ends."""
    start = 0
    end = len(value)
    while start < end and is_query_separator(value[start]):
        start += 1
    while end > start and is_query_separator(value[end - 1]):
        end -= 1
    return value[start:end]


def token_match_score(text: str, tokens: list[str]) -> int:
    lower = text.lower()
    return sum(1 for token in tokens if token in lower)


def count_occurrences(haystack: str, needle: str) -> int:
    if not needle:
        return 0
    return haystack.count(needle)
