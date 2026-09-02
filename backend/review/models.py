"""Review models — port of llm_wiki src/stores/review-store.ts and
src/lib/review-utils.ts.

Stable ids: FNV-1a over UTF-16 code units of `type::normalizedTitle`
(backend/core/hashing.py is golden-tested against the JS output).
Merge semantics: resolved state survives re-ingests ("resolved wins"),
array fields union, earliest createdAt kept.
"""

from __future__ import annotations

import re

from backend.core.hashing import fnv1a_32

# review-utils.ts REVIEW_TITLE_PREFIX_RE — common prefixes LLMs prepend
# in English or Chinese review titles.
REVIEW_TITLE_PREFIX_RE = re.compile(
    r"^(missing[\s-]?page[:：]\s*|duplicate[\s-]?page[:：]\s*|"
    r"possible[\s-]?duplicate[:：]\s*|缺失页面[:：]\s*|缺少页面[:：]\s*|"
    r"重复页面[:：]\s*|疑似重复[:：]\s*)",
    re.IGNORECASE,
)

REVIEW_TYPES = ("contradiction", "duplicate", "missing-page", "confirm", "suggestion")


def normalize_review_title(title: str) -> str:
    """Port of normalizeReviewTitle (review-utils.ts)."""
    return (
        REVIEW_TITLE_PREFIX_RE.sub("", title.lstrip())
        .replace("\n", " ")
        .strip()
    )


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def normalize_review_title_full(title: str) -> str:
    """Port of normalizeReviewTitle (review-utils.ts) including the
    whitespace collapse + lowercase steps."""
    return _collapse_whitespace(
        REVIEW_TITLE_PREFIX_RE.sub("", title.lstrip())
    ).strip().lower()


def review_id_for(review_type: str, title: str) -> str:
    """Port of reviewIdFor (review-store.ts:49-58):
    `review-{fnv1a32(type + "::" + normalizeReviewTitle(title)):08x}`."""
    key = f"{review_type}::{normalize_review_title_full(title)}"
    return f"review-{fnv1a_32(key):08x}"


def union_field(a: list[str] | None, b: list[str] | None) -> list[str] | None:
    merged = list(dict.fromkeys((a or []) + (b or [])))
    return merged if merged else None


def merge_options(a: list[dict], b: list[dict]) -> list[dict]:
    by_action: dict[str, dict] = {}
    for option in (*a, *b):
        by_action[option["action"]] = option
    return list(by_action.values())


def merge_review_items(a: dict, b: dict) -> dict:
    """Port of mergeReviewItems (review-store.ts:79-93)."""
    resolved = bool(a.get("resolved") or b.get("resolved"))
    resolved_action = (
        (a.get("resolvedAction") or b.get("resolvedAction")) if resolved else None
    )
    merged = {
        **a,  # a.id kept; both share it by construction
        "resolved": resolved,
        "description": a.get("description") or b.get("description"),
        "sourcePath": a.get("sourcePath") if a.get("sourcePath") is not None else b.get("sourcePath"),
        "affectedPages": union_field(a.get("affectedPages"), b.get("affectedPages")),
        "searchQueries": union_field(a.get("searchQueries"), b.get("searchQueries")),
        "options": merge_options(a.get("options") or [], b.get("options") or []),
        "createdAt": min(a.get("createdAt") or 0, b.get("createdAt") or 0) or None,
    }
    if resolved_action is not None:
        merged["resolvedAction"] = resolved_action
    if merged["createdAt"] is None:
        merged.pop("createdAt", None)
    return merged


def normalize_review_items(items: list[dict]) -> list[dict]:
    """Port of normalizeReviewItems (review-store.ts:96-104): remap ids
    from content, merge same-id items."""
    by_id: dict[str, dict] = {}
    for raw in items:
        remapped = {**raw, "id": review_id_for(raw.get("type", "confirm"), raw.get("title", ""))}
        existing = by_id.get(remapped["id"])
        by_id[remapped["id"]] = (
            merge_review_items(existing, remapped) if existing else remapped
        )
    return list(by_id.values())
