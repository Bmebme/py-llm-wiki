"""Ingest queue control endpoints (browser-only, mcp-server unaffected)."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from backend import config
from backend.api.router_v1 import err, ok, require_authorized
from backend.api.sse import frame, heartbeat
from backend.core import project_registry
from backend.ingest import queue as ingest_queue
from fastapi import Depends

router = APIRouter(prefix=config.API_PREFIX)
deps = [Depends(require_authorized)]


def _resolve(project_id: str) -> dict:
    try:
        return project_registry.resolve_project(project_id)
    except Exception as exc:
        raise err(404, str(exc)) from exc


@router.get("/projects/{project_id}/ingest/state", dependencies=deps)
async def ingest_state(request: Request, project_id: str) -> dict:
    project = _resolve(project_id)
    q = ingest_queue.get_queue(project["path"])
    return ok(q.state())


@router.post("/projects/{project_id}/ingest/enqueue", dependencies=deps)
async def ingest_enqueue(request: Request, project_id: str) -> dict:
    project = _resolve(project_id)
    body = await request.json()
    paths = body.get("paths")
    if not isinstance(paths, list) or not paths:
        raise err(400, "paths must be a non-empty list of project-relative source paths")
    folder_context = body.get("folderContext")
    q = ingest_queue.get_queue(project["path"])
    q.start_worker()
    tasks = await q.enqueue_batch(
        [(str(p), folder_context) for p in paths], project_id=project["id"]
    )
    return ok({"queued": [t.to_dict() for t in tasks]})


@router.post("/projects/{project_id}/ingest/pause", dependencies=deps)
async def ingest_pause(request: Request, project_id: str) -> dict:
    q = ingest_queue.get_queue(_resolve(project_id)["path"])
    await q.pause()
    return ok({})


@router.post("/projects/{project_id}/ingest/resume", dependencies=deps)
async def ingest_resume(request: Request, project_id: str) -> dict:
    q = ingest_queue.get_queue(_resolve(project_id)["path"])
    await q.resume()
    return ok({})


@router.post("/projects/{project_id}/ingest/cancel", dependencies=deps)
async def ingest_cancel(request: Request, project_id: str) -> dict:
    project = _resolve(project_id)
    body = await request.json()
    task_id = body.get("taskId")
    if not isinstance(task_id, str):
        raise err(400, "taskId is required")
    q = ingest_queue.get_queue(project["path"])
    cancelled = await q.cancel(task_id)
    if not cancelled:
        raise err(404, f"Task not found or not cancellable: {task_id}")
    return ok({})


@router.get("/projects/{project_id}/ingest/events", dependencies=deps)
async def ingest_events(request: Request, project_id: str) -> StreamingResponse:
    project = _resolve(project_id)
    q = ingest_queue.get_queue(project["path"])
    listener: asyncio.Queue = asyncio.Queue(maxsize=64)
    q.listeners.append(listener)

    async def stream():
        try:
            yield frame("meta", {"projectId": project["id"]})
            yield frame("state", q.state())
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(listener.get(), timeout=10.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield frame("agent", event)
        finally:
            if listener in q.listeners:
                q.listeners.remove(listener)

    return StreamingResponse(heartbeat_stream(stream(), interval=None), media_type="text/event-stream")


def heartbeat_stream(inner, interval):
    """Events already carry keepalives inline (10s timeout), so no
    separate heartbeat task is needed."""
    return inner
