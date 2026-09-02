"""File-change queue persistence for the file-sync surface.

Mirrors llm_wiki's `.llm-wiki/file-change-queue.json` contract: the
queue is a per-project JSON file holding FileChangeTask records. In the
Python port the actual ingestion runs through the separate ingest queue,
so the file-sync queue's job is bookkeeping: record what changed, hand
files to the ingest queue, and let the activity panel show a live view.

Status semantics for the port:
- New change tasks are created "pending", then immediately handed to
  the ingest queue and marked "done" — the ingest queue owns retries.
- retry resets a task to "pending" and re-enqueues it.
- ignore removes the task from the queue.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

QUEUE_FILE = ".llm-wiki/file-change-queue.json"
MAX_TASKS = 200
DONE_TTL_MS = 7 * 24 * 3600 * 1000

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(project_path: str) -> threading.Lock:
    key = os.path.normpath(project_path)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def queue_file_path(project_path: str) -> Path:
    return Path(project_path) / QUEUE_FILE


def read_queue(project_path: str) -> dict:
    """Read the persisted queue; returns {"version": int, "tasks": [...]}."""
    path = queue_file_path(project_path)
    if not path.exists():
        return {"version": 0, "tasks": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": 0, "tasks": []}
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        return {"version": 0, "tasks": []}
    data.setdefault("version", 0)
    return data


def write_queue(project_path: str, queue: dict) -> None:
    path = queue_file_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def new_task(project_id: str, rel_path: str, kind: str, size: int, mtime_ms: int) -> dict:
    now_ms = int(time.time() * 1000)
    return {
        "id": uuid.uuid4().hex,
        "projectId": project_id,
        "path": rel_path,
        "kind": kind,
        "status": "pending",
        "hashBefore": None,
        "hashAfter": None,
        "size": size,
        "mtimeMs": mtime_ms,
        "createdAt": now_ms,
        "updatedAt": now_ms,
        "retryCount": 0,
        "error": None,
        "needsRerun": False,
    }


def _active_key(task: dict) -> tuple:
    """Dedupe key: a pending/processing task for the same file+kind is the
    same logical change; done/failed history may accumulate."""
    status = task.get("status")
    if status in ("pending", "processing"):
        return (task.get("path"), task.get("kind"))
    return (task.get("id"),)


def _prune(queue: dict, now_ms: int) -> dict:
    tasks = queue.get("tasks", [])
    kept: list[dict] = []
    for task in tasks:
        if task.get("status") == "done":
            if now_ms - int(task.get("updatedAt") or 0) > DONE_TTL_MS:
                continue
        kept.append(task)
    kept = kept[-MAX_TASKS:]
    return {"version": int(queue.get("version", 0)) + 1, "tasks": kept}


def merge_tasks(
    project_path: str,
    project_id: str,
    tasks: list[dict],
    enqueue_cb: Callable[[list[str]], None],
) -> tuple[dict, list[dict]]:
    """Append new tasks (deduped against active ones), hand created/modified
    files to the ingest queue and mark them done.
    Returns (merged_queue, actually_added_tasks)."""
    with _lock(project_path):
        queue = read_queue(project_path)
        now_ms = int(time.time() * 1000)
        active = {_active_key(t) for t in queue.get("tasks", [])}
        added: list[dict] = []
        for task in tasks:
            key = _active_key(task)
            if key in active:
                continue
            active.add(key)
            queue["tasks"].append(task)
            added.append(task)
        if added:
            ingest_paths = [
                t["path"]
                for t in added
                if t.get("kind") in ("created", "modified")
            ]
            if ingest_paths:
                try:
                    enqueue_cb(ingest_paths)
                except Exception:
                    pass
            for task in added:
                task["status"] = "done"
                task["updatedAt"] = now_ms
        queue = _prune(queue, now_ms)
        write_queue(project_path, queue)
        return queue, added


def retry_task(
    project_path: str,
    project_id: str,
    task_id: str,
    enqueue_cb: Callable[[list[str]], None],
) -> dict:
    """Reset a task to pending and re-enqueue its file."""
    with _lock(project_path):
        queue = read_queue(project_path)
        now_ms = int(time.time() * 1000)
        for task in queue.get("tasks", []):
            if task.get("id") == task_id and task.get("projectId") == project_id:
                task["status"] = "pending"
                task["error"] = None
                task["retryCount"] = 0
                task["needsRerun"] = False
                task["updatedAt"] = now_ms
                if task.get("kind") in ("created", "modified"):
                    try:
                        enqueue_cb([task["path"]])
                    except Exception:
                        pass
                task["status"] = "done"
        queue = _prune(queue, now_ms)
        write_queue(project_path, queue)
        return queue


def ignore_task(project_path: str, project_id: str, task_id: str) -> dict:
    """Remove a task from the queue."""
    with _lock(project_path):
        queue = read_queue(project_path)
        queue["tasks"] = [
            t
            for t in queue.get("tasks", [])
            if not (t.get("id") == task_id and t.get("projectId") == project_id)
        ]
        now_ms = int(time.time() * 1000)
        queue = _prune(queue, now_ms)
        write_queue(project_path, queue)
        return queue
