"""Ingest cache — port of llm_wiki src/lib/ingest-cache.ts.

SHA256-keyed by source identity (project-relative path). A cache hit
requires EVERY previously-written file to still exist on disk —
otherwise the entry is stale and the source is fully re-ingested.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from backend.core.file_service import FsError

CACHE_FILE = "ingest-cache.json"


def _cache_path(project_path: str) -> Path:
    return Path(project_path) / ".llm-wiki" / CACHE_FILE


def _load(project_path: str) -> dict:
    try:
        parsed = json.loads(_cache_path(project_path).read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            return parsed
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save(project_path: str, data: dict) -> None:
    path = _cache_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def sha256_of(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def check_ingest_cache(
    project_path: str, source_identity: str, content: str
) -> list[str] | None:
    """Port of checkIngestCache (ingest-cache.ts:67-99).

    Returns the previously-written file list on a hit, None on miss.
    A hit requires the hash to match AND every filesWritten path to
    still exist on disk.
    """
    data = _load(project_path)
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return None
    entry = entries.get(source_identity)
    if not isinstance(entry, dict):
        return None
    stored_hash = entry.get("hash")
    files_written = entry.get("filesWritten")
    if stored_hash != sha256_of(content) or not isinstance(files_written, list):
        return None
    for rel in files_written:
        if not (Path(project_path) / rel).exists():
            return None
    return list(files_written)


def save_ingest_cache(
    project_path: str, source_identity: str, content: str, files_written: list[str]
) -> None:
    """Only called when the ingest had zero hard failures (ingest.ts:1399)."""
    data = _load(project_path)
    data.setdefault("entries", {})[source_identity] = {
        "hash": sha256_of(content),
        "timestamp": int(time.time() * 1000),
        "filesWritten": list(files_written),
    }
    _save(project_path, data)


def remove_from_ingest_cache(project_path: str, source_identity: str) -> None:
    data = _load(project_path)
    entries = data.get("entries")
    if isinstance(entries, dict):
        entries.pop(source_identity, None)
        _save(project_path, data)


def move_ingest_cache_entry(
    project_path: str, old_identity: str, new_identity: str
) -> None:
    """Keyed rename inside raw/sources (ingest-cache.ts:135-151)."""
    data = _load(project_path)
    entries = data.get("entries")
    if isinstance(entries, dict) and old_identity in entries:
        entries[new_identity] = entries.pop(old_identity)
        _save(project_path, data)


def read_cache_entries(project_path: str) -> dict:
    """Exposed for diagnostics (UI can show cached sources)."""
    return _load(project_path).get("entries", {})
