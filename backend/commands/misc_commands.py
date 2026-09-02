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
def agent_list_skills(projectPath: str) -> dict:
    return {"skills": [], "projectSkillsDir": None, "userSkillsDir": None}


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


@command("start_project_file_watcher")
def start_project_file_watcher(projectPath: str) -> str:
    from backend.ingest.watch import start_project_file_watcher as start_watch

    return start_watch(projectPath)


@command("stop_project_file_watcher")
def stop_project_file_watcher(projectPath: str) -> str:
    from backend.ingest.watch import stop_project_file_watcher as stop_watch

    return stop_watch(projectPath)


@command("rescan_project_files")
def rescan_project_files(projectPath: str) -> None:
    """Scan raw/sources and enqueue any file not yet ingested
    (cache-miss) — the deterministic rescan the API endpoint triggers."""
    import asyncio

    from pathlib import Path

    from backend.ingest import cache as ingest_cache
    from backend.ingest import queue as ingest_queue
    from backend.ingest.sources import is_ingestable_source_path

    sources_root = Path(projectPath) / "raw" / "sources"
    queue = ingest_queue.get_queue(projectPath)
    candidates = []
    if sources_root.exists():
        for entry in sorted(sources_root.rglob("*")):
            if not entry.is_file():
                continue
            rel = entry.relative_to(Path(projectPath)).as_posix()
            if not is_ingestable_source_path(rel):
                continue
            content = entry.read_text(encoding="utf-8", errors="replace")
            if ingest_cache.check_ingest_cache(projectPath, rel, content) is None:
                candidates.append(rel)
    if candidates:
        loop = asyncio.get_event_loop()
        loop.create_task(queue.enqueue_batch(candidates))
    return None


@command("get_file_change_queue")
def get_file_change_queue(**kwargs) -> list[dict]:
    return []


@command("retry_file_change_task")
def retry_file_change_task(**kwargs) -> None:
    return None


@command("ignore_file_change_task")
def ignore_file_change_task(**kwargs) -> None:
    return None
