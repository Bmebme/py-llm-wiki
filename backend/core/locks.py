"""Per-project asyncio locks — port of llm_wiki src/lib/project-mutex.ts."""

from __future__ import annotations

import asyncio

_locks: dict[str, asyncio.Lock] = {}


def project_lock(project_path: str) -> asyncio.Lock:
    key = project_path.rstrip("/")
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock
