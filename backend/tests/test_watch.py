"""Source-folder auto-watch: files dropped into raw/sources enqueue
ingest; deletions trigger the source-delete cascade."""

import asyncio
import time
from pathlib import Path

import pytest

from backend.core import project as project_core
from backend.ingest import queue as ingest_queue
from backend.ingest.watch import start_project_file_watcher, stop_project_file_watcher


@pytest.mark.asyncio
async def test_watcher_enqueues_new_source(tmp_path):
    project = project_core.create_project("watch-demo", str(tmp_path))
    project_core.ensure_project_id(project.path)
    sources_root = Path(project.path) / "raw" / "sources"
    sources_root.mkdir(parents=True, exist_ok=True)

    start_project_file_watcher(project.path)
    try:
        (sources_root / "watched-paper.md").write_text("# Watched", encoding="utf-8")
        # Debounce is 2s; poll the queue for the enqueued task.
        queue = ingest_queue.get_queue(project.path)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            await asyncio.sleep(0.3)
            if any(t.sourcePath == "raw/sources/watched-paper.md" for t in queue.tasks):
                break
        assert any(t.sourcePath == "raw/sources/watched-paper.md" for t in queue.tasks)
    finally:
        stop_project_file_watcher(project.path)


@pytest.mark.asyncio
async def test_watcher_ignores_non_ingestable(tmp_path):
    project = project_core.create_project("watch-demo2", str(tmp_path))
    project_core.ensure_project_id(project.path)
    sources_root = Path(project.path) / "raw" / "sources"
    sources_root.mkdir(parents=True, exist_ok=True)

    start_project_file_watcher(project.path)
    try:
        (sources_root / "notes.txt").write_text("hello", encoding="utf-8")
        # .txt IS ingestable (INGESTABLE_SOURCE_EXTENSIONS) — use a
        # non-ingestable extension instead to exercise the filter.
        (sources_root / "binary.xyz").write_text("junk", encoding="utf-8")
        await asyncio.sleep(2.5)
        queue = ingest_queue.get_queue(project.path)
        assert not any(t.sourcePath.endswith("binary.xyz") for t in queue.tasks)
    finally:
        stop_project_file_watcher(project.path)
