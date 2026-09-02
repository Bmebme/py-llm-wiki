r"""Wikilink helpers — shared regex from llm_wiki src/lib/graph-relevance.ts.

WIKILINK_REGEX: /\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]/g
"""

from __future__ import annotations

import re

WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]")


def extract_wikilinks(content: str) -> list[str]:
    """All wikilink targets, in document order."""
    return [match.group(1) for match in WIKILINK_RE.finditer(content)]
