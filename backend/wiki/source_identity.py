"""Source identity — port of llm_wiki src/lib/source-identity.ts.

Identity = project-relative path under raw/sources/ ("papers/foo.pdf").
Summary slugs embed per-part structural lengths + a base36 FNV-1a hash
of the identity so renames don't silently collide.
"""

from __future__ import annotations

import re
import unicodedata

from backend.core.hashing import fnv1a_32
from backend.wiki.path_utils import get_file_name, normalize_path

RAW_SOURCES_PREFIX = "raw/sources/"
RAW_SOURCES_MARKER = "/raw/sources/"
MAX_SOURCE_SUMMARY_SLUG_LENGTH = 120
FALLBACK_SOURCE_PART = "source"


def source_identity_for_path(project_path: str, source_path: str) -> str:
    pp = normalize_path(project_path).rstrip("/")
    sp = normalize_path(source_path)
    project_raw_prefix = f"{pp}/{RAW_SOURCES_PREFIX}"
    sp_key = sp.lower()
    if sp_key.startswith(project_raw_prefix.lower()):
        return sp[len(project_raw_prefix):]
    if sp_key.startswith(RAW_SOURCES_PREFIX):
        return sp[len(RAW_SOURCES_PREFIX):]
    marker_index = sp_key.find(RAW_SOURCES_MARKER)
    if marker_index >= 0:
        return sp[marker_index + len(RAW_SOURCES_MARKER):]
    return get_file_name(sp)


def source_reference_identity(source_reference: str) -> str:
    ref = normalize_path(source_reference)
    ref_key = ref.lower()
    if ref_key.startswith(RAW_SOURCES_PREFIX):
        return ref[len(RAW_SOURCES_PREFIX):]
    marker_index = ref_key.find(RAW_SOURCES_MARKER)
    if marker_index >= 0:
        return ref[marker_index + len(RAW_SOURCES_MARKER):]
    return ref


def source_summary_slug_from_identity(source_identity: str) -> str:
    without_ext = re.sub(r"\.[^/.]*$", "", source_identity)
    parts = [p.strip() for p in without_ext.split("/") if p.strip()]

    if len(parts) <= 1:
        return parts[0] if parts else "source"

    hash_slug = _stable_slug_hash(source_identity)
    slug = "--".join(
        f"{structural_length}-{readable}"
        for readable, structural_length in (
            _readable_slug_part(part) for part in parts
        )
    )
    full_slug = f"{slug}--{hash_slug}"
    if len(full_slug) <= MAX_SOURCE_SUMMARY_SLUG_LENGTH:
        return full_slug

    readable_limit = MAX_SOURCE_SUMMARY_SLUG_LENGTH - len(hash_slug) - 2
    readable_prefix = re.sub(r"-+$", "", slug[:readable_limit])
    return f"{readable_prefix or 'source'}--{hash_slug}"


def legacy_source_summary_slug_from_identity(source_identity: str) -> str:
    from urllib.parse import quote

    without_ext = re.sub(r"\.[^/.]*$", "", source_identity)
    parts = [p.strip() for p in without_ext.split("/") if p.strip()]

    if len(parts) <= 1:
        return parts[0] if parts else "source"

    hash_slug = _stable_slug_hash(source_identity)
    slug = "--".join(
        f"{len(quote(part, safe=''))}-{quote(part, safe='')}" for part in parts
    )
    return f"{slug}--{hash_slug}"


def source_summary_slug_candidates_from_identity(source_identity: str) -> list[str]:
    canonical = source_summary_slug_from_identity(source_identity)
    previous = _previous_readable_slug(source_identity)
    legacy = legacy_source_summary_slug_from_identity(source_identity)
    return list(dict.fromkeys([canonical, previous, legacy]))


def _previous_readable_slug(source_identity: str) -> str:
    without_ext = re.sub(r"\.[^/.]*$", "", source_identity)
    parts = [p.strip() for p in without_ext.split("/") if p.strip()]

    if len(parts) <= 1:
        return parts[0] if parts else "source"

    hash_slug = _stable_slug_hash(source_identity)
    slug = "--".join(
        f"{len(readable)}-{readable}"
        for readable, _ in (_readable_slug_part(part) for part in parts)
    )
    full_slug = f"{slug}--{hash_slug}"
    if len(full_slug) <= MAX_SOURCE_SUMMARY_SLUG_LENGTH:
        return full_slug

    readable_limit = MAX_SOURCE_SUMMARY_SLUG_LENGTH - len(hash_slug) - 2
    readable_prefix = re.sub(r"-+$", "", slug[:readable_limit])
    return f"{readable_prefix or 'source'}--{hash_slug}"


def _readable_slug_part(part: str) -> tuple[str, int]:
    """Port of readableSlugPart: NFKC → whitespace→`-` → keep Unicode
    letters/digits/hyphen → trim hyphens → lowercase.

    Python's str.isalnum() covers \p{L} and \p{Nd} (plus a few rare
    categories) — the documented approximation of the JS \p{L}\p{N}
    class.
    """
    structural = unicodedata.normalize("NFKC", part).strip()
    structural = re.sub(r"\s+", "-", structural)
    structural = "".join(
        ch for ch in structural if ch == "-" or ch.isalnum()
    )
    structural = structural.strip("-").lower()
    readable = re.sub(r"-+", "-", structural) or FALLBACK_SOURCE_PART
    structural_length = max(1, len(structural or FALLBACK_SOURCE_PART))
    return readable, structural_length


def _stable_slug_hash(value: str) -> str:
    """JS: (fnv1a32(value) >>> 0).toString(36)."""
    return _base36(fnv1a_32(value))


def _base36(number: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if number == 0:
        return "0"
    out = []
    while number:
        number, remainder = divmod(number, 36)
        out.append(alphabet[remainder])
    return "".join(reversed(out))
