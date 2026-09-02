"""Ingest cache + queue semantics (ingest-cache.ts / ingest-queue.ts)."""

import asyncio
import json

import pytest

from backend.ingest import cache, queue as ingest_queue
from backend.ingest.queue import (
    IngestCancelled,
    ProjectIngestQueue,
    UsageLimitError,
)


class TestIngestCache:
    def test_miss_when_no_entry(self, tmp_path):
        assert cache.check_ingest_cache(str(tmp_path), "raw/sources/a.md", "content") is None

    def test_hit_returns_files_written(self, tmp_path):
        project = tmp_path
        (project / "wiki" / "entities").mkdir(parents=True)
        (project / "wiki" / "entities" / "x.md").write_text("page")
        cache.save_ingest_cache(
            str(project), "raw/sources/a.md", "content", ["wiki/entities/x.md"]
        )
        assert cache.check_ingest_cache(str(project), "raw/sources/a.md", "content") == [
            "wiki/entities/x.md"
        ]

    def test_hash_mismatch_is_miss(self, tmp_path):
        cache.save_ingest_cache(str(tmp_path), "raw/sources/a.md", "v1", ["wiki/x.md"])
        assert cache.check_ingest_cache(str(tmp_path), "raw/sources/a.md", "v2") is None

    def test_missing_written_file_is_miss(self, tmp_path):
        cache.save_ingest_cache(
            str(tmp_path), "raw/sources/a.md", "v1", ["wiki/gone.md"]
        )
        assert cache.check_ingest_cache(str(tmp_path), "raw/sources/a.md", "v1") is None

    def test_remove_and_move(self, tmp_path):
        cache.save_ingest_cache(str(tmp_path), "raw/sources/a.md", "v1", ["wiki/x.md"])
        cache.move_ingest_cache_entry(str(tmp_path), "raw/sources/a.md", "raw/sources/b.md")
        assert cache.check_ingest_cache(str(tmp_path), "raw/sources/a.md", "v1") is None
        cache.remove_from_ingest_cache(str(tmp_path), "raw/sources/b.md")
        assert cache.read_cache_entries(str(tmp_path)) == {}


class TestIngestQueue:
    @pytest.fixture()
    async def queue(self, tmp_path):
        q = ProjectIngestQueue(str(tmp_path))
        yield q

    async def test_persist_filters_done(self, tmp_path):
        q = ProjectIngestQueue(str(tmp_path))
        await q.enqueue("raw/sources/a.md")
        await q.enqueue("raw/sources/b.md")
        q.tasks[0].status = "done"
        await q.save()
        raw = json.loads((tmp_path / ".llm-wiki" / "ingest-queue.json").read_text())
        assert len(raw) == 1
        assert raw[0]["sourcePath"] == "raw/sources/b.md"

    async def test_enqueue_dedupes_same_source(self, tmp_path):
        q = ProjectIngestQueue(str(tmp_path))
        first = await q.enqueue("raw/sources/a.md")
        second = await q.enqueue("raw/sources/a.md")
        assert first.id == second.id
        assert len(q.tasks) == 1

    async def test_restore_processing_becomes_pending(self, tmp_path):
        q = ProjectIngestQueue(str(tmp_path))
        await q.enqueue("raw/sources/a.md")
        q.tasks[0].status = "processing"
        await q.save()

        q2 = ProjectIngestQueue(str(tmp_path))
        await q2.restore()
        assert q2.tasks[0].status == "pending"
        assert q2.tasks[0].id in q2.restored_paused_task_ids

    async def test_retry_then_failed(self, tmp_path):
        q = ProjectIngestQueue(str(tmp_path))
        results = []
        q.listeners = []

        async def failing(project_path, task):
            results.append(task.retryCount)
            raise ValueError("boom")

        q.processor = failing
        await q.enqueue("raw/sources/a.md")
        for _ in range(ingest_queue.MAX_RETRIES):
            await q._worker_loop_once()
            if q.tasks[0].status == "failed":
                break

        assert q.tasks[0].status == "failed"
        assert q.tasks[0].retryCount == 3
        assert results == [0, 1, 2]
        assert "boom" in q.tasks[0].error

    async def test_usage_limit_pauses_queue(self, tmp_path):
        q = ProjectIngestQueue(str(tmp_path))

        async def limited(project_path, task):
            raise UsageLimitError("rate limit exceeded")

        q.processor = limited
        await q.enqueue("raw/sources/a.md")
        await q._worker_loop_once()
        assert q._usage_pause_until > 0
        assert q.tasks[0].status == "pending"

    async def test_cancel_processing_task(self, tmp_path):
        q = ProjectIngestQueue(str(tmp_path))

        async def cancellable(project_path, task):
            raise IngestCancelled()

        q.processor = cancellable
        task = await q.enqueue("raw/sources/a.md")
        await q._worker_loop_once()
        assert q.tasks[0].status == "cancelled"
