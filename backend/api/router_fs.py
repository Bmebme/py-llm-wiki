"""Browser-only file endpoints backing the frontend fs.ts shim.

The desktop frontend passed absolute paths to Rust commands; the web
shim keeps those signatures and the backend resolves the owning project
from the registry (see project_registry.find_owner_project).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Request

from starlette.datastructures import UploadFile

from backend import config
from backend.api.router_v1 import ApiError, err, ok, require_authorized
from backend.core import file_service, project_registry
from backend.core.file_service import FsError
from fastapi import Depends

router = APIRouter(prefix=config.API_PREFIX)
deps = [Depends(require_authorized)]


def _resolve(absolute: str) -> tuple[dict, Path]:
    try:
        project = project_registry.find_owner_project(absolute)
        path = file_service.resolve_abs(project["path"], absolute)
        return project, path
    except FsError as exc:
        raise err(400, str(exc)) from exc


@router.get("/projects/{project_id}/fs/read", dependencies=deps)
def fs_read(request: Request, project_id: str, path: str) -> dict:
    project = project_registry.resolve_project(project_id)
    try:
        abs_path = file_service.safe_join(project["path"], path)
        content = file_service.read_text(abs_path)
    except FsError as exc:
        raise err(404, str(exc)) from exc
    return ok({"content": content})


@router.get("/fs/read-abs", dependencies=deps)
def fs_read_abs(request: Request, path: str) -> dict:
    _, abs_path = _resolve(path)
    try:
        content = file_service.read_text(abs_path)
    except FsError as exc:
        raise err(404, str(exc)) from exc
    return ok({"content": content})


@router.put("/projects/{project_id}/fs/write", dependencies=deps)
async def fs_write(request: Request, project_id: str) -> dict:
    body = await request.json()
    project = project_registry.resolve_project(project_id)
    try:
        abs_path = file_service.safe_join(project["path"], body["path"])
        file_service.write_text_atomic(abs_path, body["content"])
    except (KeyError, FsError) as exc:
        raise err(400, str(exc)) from exc
    return ok({})


@router.post("/projects/{project_id}/fs/mkdir", dependencies=deps)
async def fs_mkdir(request: Request, project_id: str) -> dict:
    body = await request.json()
    project = project_registry.resolve_project(project_id)
    try:
        file_service.create_directory(file_service.safe_join(project["path"], body["path"]))
    except (KeyError, FsError) as exc:
        raise err(400, str(exc)) from exc
    return ok({})


@router.delete("/projects/{project_id}/fs/delete", dependencies=deps)
def fs_delete(request: Request, project_id: str, path: str) -> dict:
    project = project_registry.resolve_project(project_id)
    try:
        file_service.delete_file(file_service.safe_join(project["path"], path))
    except FsError as exc:
        raise err(400, str(exc)) from exc
    return ok({})


@router.post("/projects/{project_id}/fs/copy", dependencies=deps)
async def fs_copy(request: Request, project_id: str) -> dict:
    body = await request.json()
    project = project_registry.resolve_project(project_id)
    try:
        source = file_service.safe_join(project["path"], body["from"])
        destination = file_service.safe_join(project["path"], body["to"])
        created = file_service.copy_path(source, destination)
    except (KeyError, FsError) as exc:
        raise err(400, str(exc)) from exc
    return ok({"created": created} if created is not None else {})


@router.get("/projects/{project_id}/fs/stat", dependencies=deps)
def fs_stat(request: Request, project_id: str, path: str) -> dict:
    project = project_registry.resolve_project(project_id)
    try:
        stat = file_service.file_stat(file_service.safe_join(project["path"], path))
    except FsError as exc:
        raise err(400, str(exc)) from exc
    return ok(stat)


@router.get("/projects/{project_id}/fs/exists", dependencies=deps)
def fs_exists(request: Request, project_id: str, path: str) -> dict:
    project = project_registry.resolve_project(project_id)
    try:
        exists = file_service.safe_join(project["path"], path).exists()
    except FsError as exc:
        raise err(400, str(exc)) from exc
    return ok({"exists": exists})


@router.post("/projects/{project_id}/upload", dependencies=deps)
async def upload(request: Request, project_id: str, target: str = "raw/sources") -> dict:
    project = project_registry.resolve_project(project_id)
    form = await request.form()
    files = form.getlist("files")
    if not files:
        raise err(400, "No files uploaded")
    saved: list[str] = []
    for upload in files:
        if not isinstance(upload, UploadFile):
            continue
        filename = Path(upload.filename or "").name
        if not filename:
            continue
        try:
            directory = file_service.safe_join(project["path"], target)
            directory.mkdir(parents=True, exist_ok=True)
            dest = _unique_destination(directory, filename)
            with dest.open("wb") as out:
                while chunk := await upload.read(65536):
                    out.write(chunk)
            saved.append(file_service.relative_to_project(project["path"], dest))
        except FsError as exc:
            raise err(400, str(exc)) from exc
    return ok({"files": saved})


def _unique_destination(directory: Path, filename: str) -> Path:
    """Mirror the desktop's getUniqueDestPath: numeric suffix on collision."""
    dest = directory / filename
    if not dest.exists():
        return dest
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 2
    while True:
        candidate = directory / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


@router.get("/asset", dependencies=deps)
def asset(request: Request, path: str):
    """Serve project files by absolute path — the web equivalent of the
    desktop app's convertFileSrc asset URLs."""
    import mimetypes

    from fastapi.responses import FileResponse

    try:
        project = project_registry.find_owner_project(path)
        abs_path = file_service.resolve_abs(project["path"], path)
    except FsError as exc:
        raise err(400, str(exc)) from exc
    if not abs_path.is_file():
        raise err(404, "File not found")
    media_type = mimetypes.guess_type(abs_path.name)[0] or "application/octet-stream"
    return FileResponse(abs_path, media_type=media_type)


@router.get("/projects/{project_id}/download", dependencies=deps)
def download(request: Request, project_id: str, path: str):
    from fastapi.responses import FileResponse

    project = project_registry.resolve_project(project_id)
    try:
        abs_path = file_service.safe_join(project["path"], path)
    except FsError as exc:
        raise err(400, str(exc)) from exc
    if not abs_path.is_file():
        raise err(404, "File not found")
    return FileResponse(abs_path, filename=abs_path.name)
