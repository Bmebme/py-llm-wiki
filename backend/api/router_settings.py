"""Settings + project open/create endpoints for the browser frontend.

Replaces the Tauri Store plugin (app-state.json) and the create_project/
open_project Rust commands.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend import config
from backend.api.router_v1 import err, ok
from backend.core import project as project_core
from backend.core import project_registry, settings_store
from backend.core.file_service import FsError

router = APIRouter(prefix=config.API_PREFIX)


class OpenProjectBody(BaseModel):
    path: str


class CreateProjectBody(BaseModel):
    name: str
    path: str
    template: str | None = None  # named templates land later; scaffold is general


@router.get("/settings")
def get_settings() -> dict:
    state = settings_store.load() or {}
    return ok(state)


@router.put("/settings")
async def put_settings(request: Request) -> dict:
    body = await request.json()
    settings_store.save(body if isinstance(body, dict) else {})
    return ok({})


@router.get("/settings/{key:path}")
def get_settings_key(key: str) -> dict:
    state = settings_store.load() or {}
    return ok({key: state.get(key)})


@router.post("/projects/open")
def open_project(body: OpenProjectBody) -> dict:
    try:
        project = project_core.open_project(body.path)
    except FsError as exc:
        raise err(400, str(exc)) from exc
    pid = project_core.ensure_project_id(project.path)
    project_registry.register(pid, project.name, project.path)
    return ok({"id": pid, "name": project.name, "path": project.path})


@router.post("/projects/create")
def create_project(body: CreateProjectBody) -> dict:
    try:
        project = project_core.create_project(body.name, body.path)
    except FsError as exc:
        raise err(400, str(exc)) from exc
    pid = project_core.ensure_project_id(project.path)
    project_registry.register(pid, project.name, project.path)
    return ok({"id": pid, "name": project.name, "path": project.path})


@router.post("/projects/current")
def set_current_project(body: OpenProjectBody) -> dict:
    try:
        project = project_registry.resolve_project(body.path)
    except FsError as exc:
        raise err(404, str(exc)) from exc
    project_registry.set_current(project["path"])
    return ok(project)
