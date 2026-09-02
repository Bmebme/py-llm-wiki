"""19828-compatible API — port of llm_wiki src-tauri/src/api_server.rs.

This router reproduces the desktop app's local HTTP API contract so the
bundled mcp-server (and any existing client) works unchanged. Response
envelopes are `{"ok": true, ...}` / `{"ok": false, "error": ...}`.

M0 implements health/projects/files/file-content. reviews/search/graph/
sources/chat land in M1-M3.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from backend import config
from backend.api import auth
from backend.core import file_service, project_registry, settings_store
from backend.core.file_service import FsError
from backend.search.engine import DEFAULT_RESULTS

router = APIRouter(prefix=config.API_PREFIX)


def ok(body: dict) -> dict:
    return {"ok": True, **body}


class ApiError(HTTPException):
    """HTTP error carrying the {ok:false,error} envelope body."""

    def __init__(self, status: int, message: str):
        super().__init__(status_code=status, detail={"ok": False, "error": message})


def err(status: int, message: str) -> ApiError:
    return ApiError(status, message)


@router.get("/health")
def health(request: Request) -> dict:
    # /health stays reachable even when the API kill-switch is off
    # (api_server.rs:291-314).
    return ok(
        {
            "status": "running",
            "version": "0.1.0",
            "authRequired": auth.auth_required(),
            "authConfigured": auth.api_token() is not None,
            "tokenSource": auth.token_source(),
            "enabled": auth.api_enabled(),
            "mcpEnabled": auth.api_mcp_enabled(),
            "allowUnauthenticated": auth.api_allow_unauthenticated(),
            "allowLanAccess": auth.api_allow_lan_access(),
            "agent": {"chat": True, "streaming": True, "streamProtocol": "sse"},
        }
    )


async def require_api_enabled() -> None:
    if not auth.api_enabled():
        raise err(503, "API server is disabled in Settings → API Server")


async def require_authorized(request: Request) -> None:
    if not auth.is_authorized(request):
        raise err(401, "Unauthorized")


common_deps = [Depends(require_api_enabled), Depends(require_authorized)]


@router.get("/projects", dependencies=common_deps)
def handle_projects() -> dict:
    projects = project_registry.list_projects()
    current = next((p for p in projects if p["current"]), None)
    return ok({"projects": projects, "currentProject": current})


def resolve(project_id: str) -> dict:
    try:
        return project_registry.resolve_project(project_id)
    except FsError as exc:
        raise err(404, str(exc)) from exc


@router.get("/projects/{project_id}/files", dependencies=common_deps)
def handle_files(
    project_id: str,
    root: str = "wiki",
    recursive: str = "true",
    maxFiles: int = config.DEFAULT_MAX_FILES,
) -> dict:
    project = resolve(project_id)
    recursive_bool = recursive != "false"
    max_files = max(1, min(maxFiles, config.HARD_MAX_FILES))

    rel = {
        "wiki": "wiki",
        "sources": "raw/sources",
        "raw": "raw/sources",
        "raw/sources": "raw/sources",
        "all": "",
        "": "",
    }.get(root, None)
    if rel is None:
        raise err(400, "root must be wiki, sources, or all")

    try:
        if rel == "":
            files = file_service.list_public_roots(
                project["path"], recursive_bool, max_files
            )
        else:
            directory = file_service.safe_join(project["path"], rel)
            count = file_service.count_ref()
            files = file_service.list_tree(
                project["path"], directory, recursive_bool, max_files, count
            )
    except FsError as exc:
        message = str(exc)
        raise err(413 if "exceeds" in message else 400, message) from exc

    return ok(
        {
            "projectId": project["id"],
            "root": rel if rel else "all",
            "files": [to_api_file_node(f) for f in files],
            "truncated": False,
        }
    )


def to_api_file_node(node: dict) -> dict:
    """file_service returns the Tauri command shape (snake_case is_dir);
    the 19828 API contract uses camelCase isDir (api_server.rs ApiFileNode)."""
    converted = {
        "name": node["name"],
        "path": node["path"],
        "isDir": node["is_dir"],
    }
    if "size" in node:
        converted["size"] = node["size"]
    if "children" in node:
        converted["children"] = [to_api_file_node(c) for c in node["children"]]
    return converted


@router.get("/projects/{project_id}/reviews", dependencies=common_deps)
def handle_reviews(
    project_id: str,
    status: str = "unresolved",
    type: str | None = None,
    limit: int = config.DEFAULT_MAX_REVIEWS,
) -> dict:
    """Port of api_server.rs handle_reviews (1495+)."""
    from backend.review import store as review_store

    project = resolve(project_id)
    canonical = {"pending": "unresolved"}.get(status, status)
    if canonical not in ("unresolved", "resolved", "all"):
        raise err(400, "status must be unresolved, resolved, or all")
    limit = max(1, min(limit, config.HARD_MAX_REVIEWS))
    reviews = review_store.load_reviews(project["path"])
    if canonical != "all":
        wanted = canonical == "resolved"
        reviews = [r for r in reviews if bool(r.get("resolved")) is wanted]
    if type is not None:
        reviews = [r for r in reviews if r.get("type") == type]
    reviews = reviews[:limit]
    return ok({
        "projectId": project["id"],
        "status": canonical,
        "count": len(reviews),
        "reviews": reviews,
    })


@router.patch("/projects/{project_id}/reviews/{review_id}", dependencies=common_deps)
async def handle_patch_review(request: Request, project_id: str, review_id: str) -> dict:
    """Port of api_server.rs handle_patch_review: empty body = resolve;
    resolved defaults to true, pass false to reopen."""
    from backend.review import store as review_store

    project = resolve(project_id)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    resolved = body.get("resolved", True) if isinstance(body, dict) else True
    action = body.get("action") if isinstance(body, dict) else None
    patched = review_store.patch_review(project["path"], review_id, bool(resolved), action)
    if patched is None:
        raise err(404, f"Review item '{review_id}' not found")
    return ok({
        "projectId": project["id"],
        "reviewId": review_id,
        "resolved": bool(resolved),
    })


@router.post("/projects/{project_id}/reviews/resolve", dependencies=common_deps)
async def handle_bulk_resolve_reviews(request: Request, project_id: str) -> dict:
    """Port of api_server.rs handle_bulk_resolve_reviews: partial success
    is 200 with {resolved, notFound, count}."""
    from backend.review import store as review_store

    project = resolve(project_id)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise err(400, "Invalid request body") from None
    ids = body.get("ids") if isinstance(body, dict) else None
    if not isinstance(ids, list) or not ids:
        raise err(400, "ids must be a non-empty array")
    action = body.get("action") if isinstance(body, dict) else None
    result = review_store.bulk_resolve(project["path"], [str(i) for i in ids], action)
    return ok({"projectId": project["id"], **result})


@router.post("/projects/{project_id}/search", dependencies=common_deps)
async def handle_search(request: Request, project_id: str) -> dict:
    """Port of api_server.rs handle_search (1746+)."""
    project = resolve(project_id)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise err(400, "Invalid JSON body") from None
    query = body.get("query")
    if not isinstance(query, str) or not query.strip():
        raise err(400, "query is required")
    top_k = body.get("topK")
    top_k = int(top_k) if isinstance(top_k, int) or (isinstance(top_k, str) and top_k.isdigit()) else DEFAULT_RESULTS
    include_content = body.get("includeContent") is True
    query_embedding = body.get("queryEmbedding")
    from backend.search.engine import search_project_inner

    try:
        response = search_project_inner(
            project["path"], query, top_k, include_content, query_embedding
        )
    except ValueError as exc:
        raise err(400, str(exc)) from exc
    from backend.search.engine import search_response_to_api

    return ok({"projectId": project["id"], **search_response_to_api(response)})


@router.get("/projects/{project_id}/graph", dependencies=common_deps)
def handle_graph(project_id: str, q: str | None = None, nodeType: str | None = None, limit: int = 200) -> dict:
    """Port of api_server.rs handle_graph (2291-2326)."""
    project = resolve(project_id)
    from backend.search.graph import build_graph_filtered

    nodes, edges = build_graph_filtered(project["path"], q, nodeType, limit)
    return ok({"projectId": project["id"], "nodes": nodes, "edges": edges})


@router.post("/projects/{project_id}/chat", dependencies=common_deps)
async def handle_chat(request: Request, project_id: str):
    """Port of api_server.rs handle_chat / respond_chat_sse (1909-2137).

    SSE when `"stream": true` or `Accept: text/event-stream`; the
    terminal `done` frame carries the complete aggregate response.
    """
    from backend.chat.agent import AgentRequest, ChatAgent, ChatCancelled

    project = resolve(project_id)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise err(400, "Invalid JSON body") from None
    try:
        agent_request = AgentRequest.from_body(body)
    except FsError as exc:
        raise err(400, str(exc)) from exc

    wants_stream = (
        body.get("stream") is True
        or request.headers.get("accept") == "text/event-stream"
    )
    if wants_stream:
        return respond_chat_sse(project, agent_request, body)

    agent = ChatAgent(project)
    try:
        aggregate = await agent.run(agent_request)
    except ChatCancelled:
        raise err(499, "Agent turn cancelled") from None
    except FsError as exc:
        raise err(502, str(exc)) from exc
    return ok(aggregate)


class _RecorderQueue(asyncio.Queue):
    """Event queue that also keeps an ordered copy for the aggregate."""

    def __init__(self):
        super().__init__(maxsize=256)
        self.recorded: list[dict] = []

    def put_nowait(self, item):
        self.recorded.append(item)
        return super().put_nowait(item)


def respond_chat_sse(project: dict, agent_request, body: dict):
    from fastapi.responses import StreamingResponse

    from backend.api.sse import frame
    from backend.chat.agent import ChatAgent, ChatCancelled

    async def stream():
        yield frame("meta", {
            "projectId": project["id"],
            "sessionId": agent_request.session_id,
            "runId": agent_request.run_id,
        })
        queue = _RecorderQueue()
        agent = ChatAgent(project)
        agent_task = asyncio.create_task(agent.run(agent_request, events=queue))
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=10.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield frame("agent", item)
                if item.get("type") in ("done", "error"):
                    break
            try:
                aggregate = await agent_task
            except ChatCancelled:
                yield frame("cancelled", {"ok": False, "error": "Agent turn cancelled"})
                return
            except FsError as exc:
                yield frame("error", {"ok": False, "error": str(exc)})
                return
            aggregate["events"] = queue.recorded
            yield frame("done", {"ok": True, **aggregate})
        finally:
            if not agent_task.done():
                agent_task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/projects/{project_id}/chat/{session_id}/cancel", dependencies=common_deps)
def handle_cancel_chat(project_id: str, session_id: str) -> dict:
    """Port of api_server.rs handle_cancel_chat (2138+)."""
    from backend.chat.cancel import request_cancel

    project = resolve(project_id)
    cancelled = request_cancel(project["id"], session_id)
    if not cancelled:
        raise err(404, f"No active Agent turn for session: {session_id}")
    return ok({"cancelled": True})


@router.post("/projects/{project_id}/sources/rescan", dependencies=common_deps)
def handle_rescan(project_id: str) -> dict:
    """Port of api_server.rs handle_rescan: enqueue changed sources."""
    from backend.commands.misc_commands import rescan_project_files

    project = resolve(project_id)
    rescan_project_files(project["path"])
    return ok({"result": "rescan scheduled"})


@router.get("/projects/{project_id}/files/content", dependencies=common_deps)
def handle_file_content(project_id: str, path: str) -> dict:
    project = resolve(project_id)
    if not file_service.is_public_project_rel(path):
        raise err(403, "Path is not exposed by the local API")
    if not file_service.is_text_content_rel(path):
        raise err(
            415, "Only text-like project files can be read via this endpoint"
        )
    try:
        abs_path = file_service.safe_join(project["path"], path)
        content = file_service.read_text(abs_path)
    except FsError as exc:
        message = str(exc)
        if "File not found" in message or "Failed to read" in message:
            raise err(404, f"File not found: {message}") from exc
        raise err(415, message) from exc
    if len(content.encode("utf-8")) > config.MAX_FILE_CONTENT_BYTES:
        raise err(413, "File is too large to return via API")
    return ok({"projectId": project["id"], "path": path, "content": content})
