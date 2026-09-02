"""FNV-1a hashing compatible with llm_wiki's TypeScript implementations.

llm_wiki computes stable ids (review ids, source-summary slugs) with
`hash = Math.imul(h ^ charCodeAt(i), 0x01000193)` over UTF-16 code
units (src/stores/review-store.ts:49-58). Python iterates code points,
not code units, so we encode to UTF-16-LE and walk 16-bit words — this
matches charCodeAt exactly, including surrogate pairs for astral
characters.
"""

from __future__ import annotations

FNV_OFFSET_32 = 0x811C9DC5
FNV_PRIME_32 = 0x01000193
_MASK_32 = 0xFFFFFFFF


def fnv1a_32(key: str) -> int:
    """FNV-1a 32-bit over UTF-16 code units (JS charCodeAt semantics)."""
    h = FNV_OFFSET_32
    data = key.encode("utf-16-le")
    for i in range(0, len(data), 2):
        unit = data[i] | (data[i + 1] << 8)
        h ^= unit
        h = (h * FNV_PRIME_32) & _MASK_32
    return h


def fnv1a_32_hex(key: str) -> str:
    return f"{fnv1a_32(key):08x}"
