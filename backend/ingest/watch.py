"""Source-folder auto-watch — web port of llm_wiki's Rust file_sync.rs +
project-file-sync.ts. Watchdog-based: added/edited files enqueue ingest,
deleted files trigger the source-delete cascade (debounced)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from backend.ingest import queue as ingest_queue
from backend.ingest.sources import INGESTABLE_SOURCE_EXTENSIONS, is_ingestable_source_path

DEBOUNCE_SECS = 2.0

_watchers: dict[str, dict] = {}


def _is_ingestable(rel: str) -> bool:
    ext = Path(rel).suffix.lower().lstrip(".")
    return ext in INGESTABLE_SOURCE_EXTENSIONS and is_ingestable_source_path(rel)


class _SourceHandler(FileSystemEventHandler):
    def __init__(self, project_path: str, loop: asyncio.AbstractEventLoop):
        self.project_path = project_path
        self.loop = loop
        self._pending: dict[str, float] = {}
        self._timer: asyncio.Task | None = None

    def _rel(self, path: str) -> str:
        try:
            return Path(path).relative_to(self.project_path).as_posix()
        except ValueError:
            # macOS /tmp → /private/tmp: watchdog reports the resolved
            # path; fall back to resolving both sides.
            resolved_project = Path(self.project_path).resolve()
            return Path(path).resolve().relative_to(resolved_project).as_posix()

    def _note(self, rel: str, deleted: bool) -> None:
        if not _is_ingestable(rel):
            return
        self._pending[rel] = time.monotonic()
        if self._timer is None or self._timer.done():
            # Called from the watchdog thread — hop back to the main loop.
            self.loop.call_soon_threadsafe(
                lambda: setattr(self, "_timer", self.loop.create_task(self._drain(deleted)))
            )

    def on_created(self, event):
        if event.is_directory:
            return
        self._note(self._rel(event.src_path), deleted=False)

    def on_modified(self, event):
        if event.is_directory:
            return
        self._note(self._rel(event.src_path), deleted=False)

    def on_deleted(self, event):
        if event.is_directory:
            return
        self._note(self._rel(event.src_path), deleted=True)

    async def _drain(self, deleted: bool) -> None:
        await asyncio.sleep(DEBOUNCE_SECS)
        pending, self._pending = self._pending, {}
        if not pending:
            return
        queue = ingest_queue.get_queue(self.project_path)
        if deleted:
            from backend.delete.source_lifecycle import delete_source_files

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, delete_source_files, self.project_path, list(pending.keys())
            )
        else:
            await queue.enqueue_batch([(path, None) for path in pending.keys()])
            queue.start_worker()


def start_project_file_watcher(project_path: str) -> str:
    """Start (or restart) the watchdog for a project's raw/sources."""
    key = project_path.rstrip("/")
    existing = _watchers.get(key)
    if existing is not None:
        existing["observer"].stop()
        existing["observer"].join(timeout=2)
        _watchers.pop(key, None)

    sources_root = Path(project_path) / "raw" / "sources"
    sources_root.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_event_loop()
    handler = _SourceHandler(project_path, loop)
    observer = Observer()
    observer.schedule(handler, str(sources_root), recursive=True)
    observer.daemon = True
    observer.start()
    _watchers[key] = {"observer": observer, "handler": handler}
    return "started"


def stop_project_file_watcher(project_path: str) -> str:
    key = project_path.rstrip("/")
    existing = _watchers.pop(key, None)
    if existing is not None:
        existing["observer"].stop()
        existing["observer"].join(timeout=2)
        return "stopped"
    return "not-running"
