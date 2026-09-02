"""Tests for backend/file_sync/queue.py."""
from __future__ import annotations

import json

from backend.file_sync import queue as fsq


def _task(project_id="pid-1", rel="raw/sources/a.md", kind="created", status="pending"):
    t = fsq.new_task(project_id, rel, kind, size=10, mtime_ms=1000)
    t["status"] = status
    return t


def test_merge_creates_queue_file_and_marks_done(tmp_path):
    project = str(tmp_path)
    enqueued: list[list[str]] = []

    queue, added = fsq.merge_tasks(project, "pid-1", [_task()], enqueued.append)

    assert len(added) == 1
    assert added[0]["status"] == "done"
    assert queue["tasks"][0]["status"] == "done"
    assert enqueued == [["raw/sources/a.md"]]
    persisted = json.loads(
        (tmp_path / ".llm-wiki" / "file-change-queue.json").read_text(encoding="utf-8")
    )
    assert persisted["tasks"][0]["id"] == added[0]["id"]


def test_merge_dedupes_duplicates_within_batch(tmp_path):
    """同一批次内同一文件重复出现 → 只入队一次。
    （跨批次再次变更应产生新任务，那是合法的新变更。）"""
    project = str(tmp_path)
    first = _task()
    dup = _task()
    queue, added = fsq.merge_tasks(project, "pid-1", [first, dup], lambda paths: None)

    assert len(added) == 1
    assert len(queue["tasks"]) == 1


def test_retry_resets_status_and_reenqueues(tmp_path):
    project = str(tmp_path)
    queue, added = fsq.merge_tasks(project, "pid-1", [_task()], lambda paths: None)
    task_id = added[0]["id"]
    reenqueued: list[list[str]] = []

    queue = fsq.retry_task(project, "pid-1", task_id, reenqueued.append)

    task = next(t for t in queue["tasks"] if t["id"] == task_id)
    assert task["status"] == "done"
    assert reenqueued == [["raw/sources/a.md"]]


def test_ignore_removes_task(tmp_path):
    project = str(tmp_path)
    queue, added = fsq.merge_tasks(project, "pid-1", [_task()], lambda paths: None)
    task_id = added[0]["id"]

    queue = fsq.ignore_task(project, "pid-1", task_id)

    assert all(t["id"] != task_id for t in queue["tasks"])


def test_read_queue_missing_file_returns_empty(tmp_path):
    assert fsq.read_queue(str(tmp_path / "nope")) == {"version": 0, "tasks": []}


def test_read_queue_corrupt_file_returns_empty(tmp_path):
    project = tmp_path
    qf = project / ".llm-wiki" / "file-change-queue.json"
    qf.parent.mkdir(parents=True)
    qf.write_text("{not json", encoding="utf-8")
    assert fsq.read_queue(str(project)) == {"version": 0, "tasks": []}
