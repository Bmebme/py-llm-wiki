"""Miscellaneous command stubs for features deferred beyond v0.

Each returns the shape the frontend expects so the UI renders sanely
instead of erroring; real implementations land in their milestones.
"""

from __future__ import annotations

from backend.core import settings_store

COMMANDS: dict[str, callable] = {}


def command(name: str):
    def decorator(func):
        COMMANDS[name] = func
        return func

    return decorator


# --- server status (the web port *is* the server) ------------------------


@command("api_server_status")
def api_server_status() -> str:
    return "running"


@command("api_server_reload_config")
def api_server_reload_config() -> str:
    settings_store.invalidate_cache()
    return "reloaded"


@command("clip_server_status")
def clip_server_status() -> str:
    return "Web clipper is not available in the browser port (v1)"


@command("mcp_server_entry_path")
def mcp_server_entry_path() -> str:
    return ""  # M4: resolve the bundled mcp-server path


# --- environment ----------------------------------------------------------


@command("set_close_behavior")
def set_close_behavior(value: str) -> None:
    return None  # browser tab close needs no confirmation dialog


@command("set_proxy_env")
def set_proxy_env(config: dict) -> None:
    return None  # backend-originated calls read proxy config directly (M4)


# --- LLM CLI transports (deferred) ----------------------------------------


@command("claude_cli_detect")
def claude_cli_detect() -> dict:
    return {"found": False, "version": None}


@command("codex_cli_detect")
def codex_cli_detect() -> dict:
    return {"found": False, "version": None}


# --- agent (M2) ------------------------------------------------------------


@command("agent_list_skills")
def agent_list_skills(projectPath: str) -> list[dict]:
    """扫描可用 Agent Skills，返回 [{id, name, description, source}] 数组。

    目录优先级（高 → 低，id 相同者先占位）：
      项目级: <project>/.llm-wiki/skills   (source: project)
      用户级: ~/.claude/skills (claude) · ~/.codex/skills (codex) · ~/.agents/skills (agents)
    每个技能是一个包含 SKILL.md 的子目录；name/description 取自 frontmatter。
    """
    import re
    from pathlib import Path

    roots: list[tuple[Path, str]] = []
    if projectPath:
        roots.append((Path(projectPath) / ".llm-wiki" / "skills", "project"))
    home = Path.home()
    for sub, src in ((".claude", "claude"), (".codex", "codex"), (".agents", "agents")):
        roots.append((home / sub / "skills", src))

    def _parse_frontmatter(text: str) -> tuple[str, str]:
        name, description = "", ""
        m = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
        if m:
            name = m.group(1).strip().strip("\"'")
        # 处理 YAML 块标量（description: |- 后续缩进行拼接）与单行两种形态
        m = re.search(r"^description:\s*([|>]-?)\s*$", text, re.MULTILINE)
        if m:
            body: list[str] = []
            for line in text[m.end():].splitlines():
                if not line.strip():
                    continue
                if line.startswith((" ", "\t")):
                    body.append(line.strip())
                else:
                    break
            description = " ".join(body)
        else:
            m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
            if m:
                description = m.group(1).strip().strip("\"'")
        return name, description

    skills: dict[str, dict] = {}
    for root, source in roots:
        if not root.is_dir():
            continue
        for skill_dir in sorted(root.iterdir()):
            if not skill_dir.is_dir():
                continue
            main = skill_dir / "SKILL.md"
            if not main.is_file():
                continue
            try:
                text = main.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name, description = _parse_frontmatter(text)
            slug = skill_dir.name
            if slug not in skills:  # 优先级高者先占位，项目级覆盖用户级
                skills[slug] = {
                    "id": slug,
                    "name": name or slug,
                    "description": description or "",
                    "source": source,
                }
    return list(skills.values())


# --- search / embeddings / vector store (M2 / v1) --------------------------


@command("search_project")
def search_project(
    projectPath: str,
    query: str,
    topK: int | None = None,
    includeContent: bool | None = None,
    queryEmbedding=None,
    embeddingConfig=None,
) -> dict:
    """Frontend searchWiki() invokes this with the BackendSearchResponse
    shape (src/lib/search.ts) — camelCase, project-relative paths."""
    from backend.search.engine import search_project_inner, search_response_to_api

    response = search_project_inner(
        projectPath, query, topK or 20, bool(includeContent), queryEmbedding
    )
    return search_response_to_api(response)


@command("web_search")
def web_search(**kwargs) -> list[dict]:
    raise NotImplementedError("web_search lands after v0 (Deep Research)")


@command("anytxt_search")
def anytxt_search(**kwargs) -> list[dict]:
    raise NotImplementedError("anytxt_search lands after v0")


@command("embedding_fetch")
def embedding_fetch(**kwargs) -> list[float]:
    raise NotImplementedError("embeddings land after v0 (vector search)")


@command("embedding_fetch_batch")
def embedding_fetch_batch(**kwargs) -> list[list[float]]:
    raise NotImplementedError("embeddings land after v0 (vector search)")


@command("vector_upsert_chunks")
def vector_upsert_chunks(**kwargs) -> None:
    raise NotImplementedError("vector store lands after v0")


@command("vector_search_chunks")
def vector_search_chunks(**kwargs) -> list[dict]:
    raise NotImplementedError("vector store lands after v0")


@command("vector_delete_page")
def vector_delete_page(**kwargs) -> None:
    raise NotImplementedError("vector store lands after v0")


@command("vector_count_chunks")
def vector_count_chunks(**kwargs) -> int:
    raise NotImplementedError("vector store lands after v0")


@command("vector_clear_chunks")
def vector_clear_chunks(**kwargs) -> None:
    raise NotImplementedError("vector store lands after v0")


@command("vector_optimize_chunks")
def vector_optimize_chunks(**kwargs) -> None:
    raise NotImplementedError("vector store lands after v0")


@command("vector_legacy_row_count")
def vector_legacy_row_count(**kwargs) -> int:
    return 0


@command("vector_drop_legacy")
def vector_drop_legacy(**kwargs) -> None:
    return None


# --- maintenance (M5) -------------------------------------------------------


@command("rebuild_wiki_index")
def rebuild_wiki_index(projectPath: str) -> dict:
    from backend.delete.archive import rebuild_wiki_index as rebuild

    return rebuild(projectPath)


@command("export_project_archive")
def export_project_archive(projectPath: str, destination: str) -> str:
    from backend.delete.archive import export_project_archive as export_archive

    export_archive(projectPath, destination)
    return destination


@command("import_project_archive")
def import_project_archive(archivePath: str, destination: str) -> str:
    from backend.core import project as project_core
    from backend.core import project_registry
    from backend.delete.archive import import_project_archive as import_archive

    path = import_archive(archivePath, destination)
    project = project_core.open_project(path)
    pid = project_core.ensure_project_id(path)
    project_registry.register(pid, project.name, path)
    return path


# --- file watcher (M5) ------------------------------------------------------


def _watcher_project_path(projectId: str = "", projectPath: str = "") -> str:
    """前端可能传 projectId（UUID/路径/current）、projectPath 或都不传。
    优先 projectPath；其次按 projectId 解析；最后用注册表当前项目。"""
    if projectPath:
        return projectPath
    if projectId:
        try:
            from backend.core import project_registry

            return project_registry.resolve_project(projectId)["path"]
        except Exception:
            pass
    from backend.core import project_registry

    return project_registry.current_project_path()


def _project_id_for(projectId: str, projectPath: str) -> str:
    try:
        from backend.core import project_registry

        if projectId:
            return project_registry.resolve_project(projectId)["id"]
        if projectPath:
            return project_registry.resolve_project(projectPath)["id"]
        return project_registry.resolve_project(
            project_registry.current_project_path()
        )["id"]
    except Exception:
        return projectId or projectPath


def _ingest_enqueue_cb(project_path: str):
    """返回一个回调：把变更文件排进 ingest 队列（调度在事件循环上执行）。"""
    import asyncio

    from backend.ingest import queue as ingest_queue

    q = ingest_queue.get_queue(project_path)

    def cb(paths: list[str]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        loop.create_task(q.enqueue_batch(paths))

    return cb


def _rescan_result(project_id: str, project_path: str) -> dict:
    """启动时扫描 raw/sources 中未摄入的文件（cache-miss），合并进持久化
    变更队列（.llm-wiki/file-change-queue.json）并交给 ingest 队列；
    返回与前端 FileChangeRescanResult 对齐的结构（queue + changedTasks）。"""
    from pathlib import Path

    from backend.file_sync import queue as fs_queue
    from backend.ingest import cache as ingest_cache
    from backend.ingest.sources import is_ingestable_source_path

    sources_root = Path(project_path) / "raw" / "sources"
    tasks: list[dict] = []
    if sources_root.exists():
        for entry in sorted(sources_root.rglob("*")):
            if not entry.is_file():
                continue
            rel = entry.relative_to(Path(project_path)).as_posix()
            if not is_ingestable_source_path(rel):
                continue
            content = entry.read_text(encoding="utf-8", errors="replace")
            if ingest_cache.check_ingest_cache(project_path, rel, content) is not None:
                continue
            st = entry.stat()
            tasks.append(
                fs_queue.new_task(
                    project_id, rel, "created", st.st_size, int(st.st_mtime * 1000)
                )
            )
    if not tasks:
        queue = fs_queue.read_queue(project_path)
        return {"queue": queue, "changedTasks": []}
    queue, added = fs_queue.merge_tasks(project_path, project_id, tasks)
    return {"queue": queue, "changedTasks": added}


@command("start_project_file_watcher")
def start_project_file_watcher(
    projectId: str = "", projectPath: str = "", sourceWatchConfig: dict | None = None
) -> dict:
    from backend.ingest.watch import start_project_file_watcher as start_watch

    path = _watcher_project_path(projectId, projectPath)
    start_watch(path)
    return _rescan_result(_project_id_for(projectId, path), path)


@command("stop_project_file_watcher")
def stop_project_file_watcher(projectId: str = "", projectPath: str = "") -> str:
    from backend.ingest.watch import stop_project_file_watcher as stop_watch

    path = _watcher_project_path(projectId, projectPath)
    return stop_watch(path) if path else "not-running"


@command("rescan_project_files")
def rescan_project_files(
    projectId: str = "", projectPath: str = "", sourceWatchConfig: dict | None = None
) -> dict:
    """Scan raw/sources and enqueue any file not yet ingested (cache-miss)。
    返回与前端 FileChangeRescanResult 对齐的结构。
    projectId/sourceWatchConfig 为前端传入的附加参数。"""
    path = _watcher_project_path(projectId, projectPath)
    return _rescan_result(_project_id_for(projectId, path), path)


@command("get_file_change_queue")
def get_file_change_queue(projectPath: str = "") -> dict:
    from backend.file_sync import queue as fs_queue

    path = _watcher_project_path("", projectPath)
    if not path:
        return {"version": 0, "tasks": []}
    return fs_queue.read_queue(path)


@command("retry_file_change_task")
def retry_file_change_task(
    projectId: str = "", projectPath: str = "", taskId: str = ""
) -> dict:
    from backend.file_sync import queue as fs_queue

    path = _watcher_project_path(projectId, projectPath)
    if not path or not taskId:
        return {"version": 0, "tasks": []}
    return fs_queue.retry_task(
        path, _project_id_for(projectId, path), taskId, _ingest_enqueue_cb(path)
    )


@command("ignore_file_change_task")
def ignore_file_change_task(
    projectId: str = "", projectPath: str = "", taskId: str = ""
) -> dict:
    from backend.file_sync import queue as fs_queue

    path = _watcher_project_path(projectId, projectPath)
    if not path or not taskId:
        return {"version": 0, "tasks": []}
    return fs_queue.ignore_task(path, _project_id_for(projectId, path), taskId)
