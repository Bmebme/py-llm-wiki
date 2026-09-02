"""Filename generation for user-initiated wiki writes ("Save to Wiki")
— port of llm_wiki src/lib/wiki-filename.ts.

Shape: {slug}-{YYYY-MM-DD}-{HHMMSS}.md with a Unicode-aware slug
(NFKC, whitespace→hyphen, keep letters/digits, ≤50 chars, "query"
fallback) and a UTC timestamp so same-day saves stay distinct.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone

MAX_SLUG_CHARS = 50


def make_query_slug(title: str) -> str:
    slug = unicodedata.normalize("NFKC", title).strip()
    slug = re.sub(r"\s+", "-", slug)
    # Keep Unicode letters, Unicode digits, and the ASCII hyphen.
    slug = "".join(ch for ch in slug if ch == "-" or ch.isalnum())
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-").lower()
    truncated = slug[:MAX_SLUG_CHARS]  # len() counts code points, like Array.from
    return truncated if truncated else "query"


def make_query_file_name(
    title: str, now: datetime | None = None
) -> dict:
    """Port of makeQueryFileName — UTC timestamp to avoid DST flips."""
    slug = make_query_slug(title)
    iso = (now or datetime.now(timezone.utc)).isoformat()
    date = iso[:10]
    time = iso[11:19].replace(":", "")
    return {
        "slug": slug,
        "date": date,
        "time": time,
        "fileName": f"{slug}-{date}-{time}.md",
    }
