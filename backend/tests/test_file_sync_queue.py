"""Tests for backend/file_sync/queue.py."""
from __future__ import annotations

import json
import time

from backend.file_sync import queue as fsq


def _task(project_id="pid-1", rel="raw/sources/a.md", kind="created", status="pending"):
    t = fsq.new_task(project_id, rel, kind, size=10, mtime_ms=1000)
    t["status"] = status
    return t


def test_merge_creates_queue_file_and_marks_done(tmp_path):
    project = str(tmp_path)

    queue, added = fsq.merge_tasks(project, "pid-1", [_task()])

    assert len(added) == 1
    assert added[0]["status"] == "done"
    assert queue["tasks"][0]["status"] == "done"
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
    queue, added = fsq.merge_tasks(project, "pid-1", [first, dup])

    assert len(added) == 1
    assert len(queue["tasks"]) == 1


def test_merge_blocks_failed_duplicates(tmp_path):
    """失败任务未解决时，同一文件再次 rescan 不新增任务（防队列膨胀）。"""
    project = str(tmp_path)
    first = _task()
    fsq.merge_tasks(project, "pid-1", [first])
    # 模拟任务失败：手动写回 failed 状态
    with fsq._lock(project):
        queue = fsq.read_queue(project)
        queue["tasks"][0]["status"] = "failed"
        fsq.write_queue(project, queue)

    queue, added = fsq.merge_tasks(project, "pid-1", [_task()])

    assert added == []
    assert len(queue["tasks"]) == 1


def test_retry_resets_status_and_reenqueues(tmp_path):
    project = str(tmp_path)
    queue, added = fsq.merge_tasks(project, "pid-1", [_task()])
    task_id = added[0]["id"]
    reenqueued: list[list[str]] = []

    queue = fsq.retry_task(project, "pid-1", task_id, reenqueued.append)

    task = next(t for t in queue["tasks"] if t["id"] == task_id)
    assert task["status"] == "done"
    assert reenqueued == [["raw/sources/a.md"]]


def test_merge_skips_unchanged_done_files(tmp_path):
    """文件 mtime 未变化且已有 done 记录 → 下次扫描不重复记账。"""
    project = str(tmp_path)
    first = _task()
    fsq.merge_tasks(project, "pid-1", [first])

    # 同一 mtime 的文件再次扫描 → 跳过
    queue, added = fsq.merge_tasks(project, "pid-1", [_task()])
    assert added == []
    assert len(queue["tasks"]) == 1

    # 文件真的变了（mtime 不同）→ 正常新增
    changed = _task()
    changed["mtimeMs"] = 2000
    queue, added = fsq.merge_tasks(project, "pid-1", [changed])
    assert len(added) == 1
    assert len(queue["tasks"]) == 2


def test_prune_dedupes_duplicate_done_history(tmp_path):
    """同一文件同 mtime 的多条 done 历史 → 只保留最新一条。"""
    project = str(tmp_path)
    for _ in range(3):
        fsq.merge_tasks(project, "pid-1", [_task()])
    # 手动塞入重复 done 历史（模拟旧版本留下的脏数据）
    with fsq._lock(project):
        queue = fsq.read_queue(project)
        dup = dict(queue["tasks"][-1])
        dup["id"] = "dup-id"
        queue["tasks"].append(dup)
        fsq.write_queue(project, queue)
        pruned = fsq._prune(queue, int(time.time() * 1000))
    assert len(pruned["tasks"]) == 1


def test_ignore_removes_task(tmp_path):
    project = str(tmp_path)
    queue, added = fsq.merge_tasks(project, "pid-1", [_task()])
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


# --- agent_list_skills (misc_commands) ---


def test_agent_list_skills_scans_and_prioritizes(tmp_path, monkeypatch):
    from backend.commands import misc_commands

    project = tmp_path / "proj"
    (project / ".llm-wiki" / "skills" / "demo" / "SKILL.md").parent.mkdir(parents=True)
    (project / ".llm-wiki" / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: Demo Skill\ndescription: does demo\n---\n", encoding="utf-8"
    )
    # 用户级同名技能应被项目级覆盖
    fake_home = tmp_path / "home"
    (fake_home / ".claude" / "skills" / "demo" / "SKILL.md").parent.mkdir(parents=True)
    (fake_home / ".claude" / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: User Demo\n---\n", encoding="utf-8"
    )
    (fake_home / ".claude" / "skills" / "other" / "SKILL.md").parent.mkdir(parents=True)
    (fake_home / ".claude" / "skills" / "other" / "SKILL.md").write_text(
        "---\nname: Other\n---\n", encoding="utf-8"
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)

    result = misc_commands.COMMANDS["agent_list_skills"](projectPath=str(project))

    assert isinstance(result, list)
    by_id = {s["id"]: s for s in result}
    assert set(by_id) == {"demo", "other"}
    assert by_id["demo"]["name"] == "Demo Skill"          # 项目级覆盖用户级
    assert by_id["demo"]["source"] == "project"
    assert by_id["other"]["source"] == "claude"
