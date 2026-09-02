"""Ingest queue — port of llm_wiki src/lib/ingest-queue.ts.

Persisted to `<project>/.llm-wiki/ingest-queue.json`. Semantics kept
from the desktop app:
- done tasks filtered out on save (serialized single-writer snapshots)
- enqueue dedupes by source path and promotes pending
- MAX_RETRIES = 3, then failed
- provider usage-limit errors pause the queue, auto-resume after 15 min
- restore on startup: processing → pending, restored tasks NOT auto-run
- worker limit 1 (v0); cancel retains the task with a cancelled flag
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from backend.core.file_service import FsError
from backend.llm.client import UsageLimitError

MAX_RETRIES = 3
USAGE_LIMIT_AUTO_RESUME_SECS = 15 * 60

# The desktop regex for provider usage-limit errors (ingest-queue.ts).
USAGE_LIMIT_RE = re.compile(
    r"(rate\s*limit|quota|429|insufficient_quota|usage\s*limit|"
    r"exceeded.*(?:limit|quota)|billing|overloaded|503 service unavailable)",
    re.IGNORECASE,
)


@dataclass
class IngestTask:
    id: str
    projectId: str
    sourcePath: str  # project-relative
    folderContext: str | None = None
    status: str = "pending"  # pending|processing|done|failed|cancelled
    addedAt: int = 0
    error: str | None = None
    retryCount: int = 0
    autoStart: bool | None = None

    def to_dict(self) -> dict:
        out = {
            "id": self.id,
            "projectId": self.projectId,
            "sourcePath": self.sourcePath,
            "status": self.status,
            "addedAt": self.addedAt,
            "retryCount": self.retryCount,
        }
        if self.folderContext is not None:
            out["folderContext"] = self.folderContext
        if self.error is not None:
            out["error"] = self.error
        if self.autoStart is not None:
            out["autoStart"] = self.autoStart
        return out

    @classmethod
    def from_dict(cls, raw: dict) -> "IngestTask":
        return cls(
            id=str(raw.get("id") or uuid.uuid4().hex),
            projectId=str(raw.get("projectId") or ""),
            sourcePath=str(raw.get("sourcePath") or ""),
            folderContext=raw.get("folderContext"),
            status=str(raw.get("status") or "pending"),
            addedAt=int(raw.get("addedAt") or 0),
            error=raw.get("error"),
            retryCount=int(raw.get("retryCount") or 0),
            autoStart=raw.get("autoStart"),
        )


class ProjectIngestQueue:
    """Per-project queue with a single worker."""

    def __init__(self, project_path: str):
        self.project_path = project_path.rstrip("/")
        self.tasks: list[IngestTask] = []
        self._restored = False
        self.paused = False
        self.restored_paused_task_ids: set[str] = set()
        self.worker_busy = False
        self.epoch = 0  # bumped on every state change; guards stale saves
        self.last_error: str | None = None
        self._usage_pause_until: float = 0.0
        self._write_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._worker_task: asyncio.Task | None = None
        # Set by cancel() when the processing task is cancelled; the
        # pipeline polls it between LLM calls for cooperative cancel.
        self._cancel_requested = asyncio.Event()
        # Event sink for the SSE progress stream (router_ingest).
        self.listeners: list[asyncio.Queue] = []
        self.processor = _DEFAULT_PROCESSOR  # injected by backend.ingest.pipeline

    # --- persistence ------------------------------------------------------

    def _queue_file(self) -> Path:
        return Path(self.project_path) / ".llm-wiki" / "ingest-queue.json"

    async def _save_locked(self) -> None:
        """Serialized snapshot; done tasks filtered out (ingest-queue.ts:109)."""
        payload = [t.to_dict() for t in self.tasks if t.status != "done"]
        path = self._queue_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    async def save(self) -> None:
        async with self._write_lock:
            await self._save_locked()

    async def restore(self) -> None:
        """Port of restoreQueue (ingest-queue.ts:769-903): processing →
        pending, restored tasks are NOT auto-run.

        采用按 id 合并语义而非整体覆盖：内存中已有的任务（可能正在被
        处理或刚入队）保留其最新状态，只补充磁盘上独有的遗留任务。
        避免恢复动作与正在运行的任务互相踩踏。"""
        try:
            raw = json.loads(self._queue_file().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, list):
            return
        disk: list[IngestTask] = []
        for entry in raw:
            task = IngestTask.from_dict(entry)
            if task.status == "processing":
                task.status = "pending"
            disk.append(task)
        mem_by_id = {t.id: t for t in self.tasks}
        merged: list[IngestTask] = []
        for task in disk:
            merged.append(mem_by_id.pop(task.id, task))
        # 内存中磁盘上没有的新任务（恢复期间刚入队的）追加在尾部
        merged.extend(mem_by_id.values())
        self.tasks = merged
        self.restored_paused_task_ids = {t.id for t in disk}
        self.epoch += 1

    async def restore_and_start(self) -> None:
        """Restore persisted tasks and start the worker (startup path)."""
        if self._restored:
            return
        self._restored = True
        await self.restore()
        self.start_worker()

    # --- mutation ---------------------------------------------------------

    def _same_queued_source_path(self, source_path: str) -> IngestTask | None:
        for t in self.tasks:
            if t.status in ("pending", "processing") and t.sourcePath == source_path:
                return t
        return None

    async def enqueue(
        self,
        source_path: str,
        folder_context: str | None = None,
        auto_start: bool | None = None,
        project_id: str = "",
    ) -> IngestTask:
        existing = self._same_queued_source_path(source_path)
        if existing is not None:
            return existing
        task = IngestTask(
            id=uuid.uuid4().hex,
            projectId=project_id,
            sourcePath=source_path,
            folderContext=folder_context,
            status="pending",
            addedAt=int(time.time() * 1000),
            autoStart=auto_start,
        )
        self.tasks.append(task)
        self.epoch += 1
        await self.save()
        await self._emit({"kind": "enqueued", "task": task.to_dict()})
        self._wake.set()
        self.start_worker()
        return task

    async def enqueue_batch(
        self, sources: list[tuple[str, str | None]], project_id: str = ""
    ) -> list[IngestTask]:
        added: list[IngestTask] = []
        for source_path, folder_context in sources:
            if self._same_queued_source_path(source_path) is not None:
                continue
            task = IngestTask(
                id=uuid.uuid4().hex,
                projectId=project_id,
                sourcePath=source_path,
                folderContext=folder_context,
                status="pending",
                addedAt=int(time.time() * 1000),
            )
            self.tasks.append(task)
            added.append(task)
        if added:
            self.epoch += 1
            await self.save()
            for task in added:
                await self._emit({"kind": "enqueued", "task": task.to_dict()})
            self._wake.set()
            self.start_worker()
        return added

    async def cancel(self, task_id: str) -> bool:
        for t in self.tasks:
            if t.id == task_id and t.status in ("pending", "processing"):
                if t.status == "pending":
                    t.status = "cancelled"
                else:
                    # Processing tasks are cancelled cooperatively: the
                    # pipeline polls _cancel_requested between LLM calls.
                    self._cancel_requested.set()
                self.epoch += 1
                await self._emit({"kind": "cancelled", "task": t.to_dict()})
                return True
        return False

    async def discard_for_sources(self, source_paths: list[str]) -> None:
        for t in list(self.tasks):
            if t.sourcePath in source_paths and t.status in ("pending", "failed", "cancelled"):
                self.tasks.remove(t)
        self.epoch += 1
        await self.save()

    async def pause(self) -> None:
        self.paused = True
        await self._emit({"kind": "paused"})

    async def resume(self) -> None:
        self.paused = False
        self._usage_pause_until = 0.0
        await self._emit({"kind": "resumed"})
        self._wake.set()

    # --- events ------------------------------------------------------------

    async def _emit(self, event: dict) -> None:
        for queue in list(self.listeners):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    # --- worker ------------------------------------------------------------

    def start_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.get_event_loop().create_task(self._worker_loop())

    async def _worker_loop_once(self) -> bool:
        """One processor pass over the next runnable task (shared by the
        worker loop and tests). Returns True when a task was processed."""
        task = self._next_runnable()
        if task is None:
            return False
        self.worker_busy = True
        self._cancel_requested.clear()
        task.status = "processing"
        await self.save()
        await self._emit({"kind": "processing", "task": task.to_dict()})
        if self.processor is None:
            task.status = "pending"
            self.worker_busy = False
            await self.save()
            return True
        try:
            result = await self.processor(self.project_path, task)
        except UsageLimitError:
            task.status = "pending"
            self._usage_pause_until = time.monotonic() + USAGE_LIMIT_AUTO_RESUME_SECS
            await self._emit({"kind": "usage-limited", "resumeIn": USAGE_LIMIT_AUTO_RESUME_SECS})
        except IngestCancelled:
            task.status = "cancelled"
            await self._emit({"kind": "cancelled", "task": task.to_dict()})
        except Exception as exc:  # noqa: BLE001 - queue must survive processor bugs
            task.retryCount += 1
            if task.retryCount >= MAX_RETRIES:
                task.status = "failed"
                task.error = str(exc)
                self.last_error = str(exc)
                await self._emit({"kind": "failed", "task": task.to_dict(), "error": str(exc)})
            else:
                task.status = "pending"
                await self._emit({
                    "kind": "retry",
                    "task": task.to_dict(),
                    "error": str(exc),
                })
        else:
            task.status = "done"
            self.last_error = None
            await self._emit({"kind": "done", "task": task.to_dict()})
            if result:
                await self._emit({"kind": "files-written", "paths": result})
        finally:
            self.worker_busy = False
            self.epoch += 1
            await self.save()
        return True

    async def _worker_loop(self) -> None:
        while True:
            if self.paused or time.monotonic() < self._usage_pause_until:
                await self._wake.wait()
                self._wake.clear()
                continue
            processed = await self._worker_loop_once()
            if not processed:
                await self._wake.wait()
                self._wake.clear()
                continue
            # Small yield so cancel/enqueue requests get serviced.
            await asyncio.sleep(0)

    def _next_runnable(self) -> IngestTask | None:
        for t in self.tasks:
            if t.status == "pending":
                return t
        return None

    def get_processing_task(self) -> IngestTask | None:
        for t in self.tasks:
            if t.status == "processing":
                return t
        return None

    def state(self) -> dict:
        return {
            "queue": [t.to_dict() for t in self.tasks if t.status != "done"],
            "paused": self.paused,
            "workerBusy": self.worker_busy,
            "lastError": self.last_error,
        }


class IngestCancelled(Exception):
    """Task was cancelled by the user mid-processing."""


_registry: dict[str, ProjectIngestQueue] = {}
_DEFAULT_PROCESSOR = None  # set by backend.ingest.pipeline.bind_queue_processors


def get_queue(project_path: str) -> ProjectIngestQueue:
    key = project_path.rstrip("/")
    queue = _registry.get(key)
    if queue is None:
        queue = ProjectIngestQueue(key)
        queue.processor = _DEFAULT_PROCESSOR
        _registry[key] = queue
        # 启动路径：恢复磁盘上遗留的任务（processing → pending）并拉起 worker。
        # 否则重启后内存队列为空，磁盘上的任务永远不会被处理。
        loop = asyncio.get_event_loop()
        loop.create_task(queue.restore_and_start())
    return queue
