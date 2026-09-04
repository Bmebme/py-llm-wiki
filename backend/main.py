"""py-llm-wiki — FastAPI entry point.

Single-user, localhost-first port of llm_wiki. Run with:

    python -m backend.main
"""

from __future__ import annotations

import time
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend import config
from backend.api import (
    router_fs,
    router_ingest,
    router_llm,
    router_settings,
    router_tauri,
    router_v1,
)
from backend.api.router_v1 import ApiError
from backend.core import project_registry, settings_store
from backend.ingest import pipeline as ingest_pipeline
from backend.ingest import queue as ingest_queue


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_data_dir()
    # 统一 LLM 约定: 环境变量覆盖 llmConfig (容器化部署注入, 免改 app-state)
    llm_override = config.env_llm_override()
    if llm_override:
        state0 = settings_store.load() or {}
        llm_cfg = dict(state0.get("llmConfig") or {})
        llm_cfg.update(llm_override)
        state0["llmConfig"] = llm_cfg
        settings_store.save(state0)
    # Restore the last-opened project as "current" (mirrors the desktop
    # app reopening lastProject at launch).
    state = settings_store.load() or {}
    last = state.get("lastProject")
    if isinstance(last, str) and last:
        try:
            project_registry.set_current(last)
        except Exception:  # noqa: BLE001 - a stale path must not block startup
            pass
    # Attach the ingest pipeline to queues and restore persisted queues
    # for registered projects (restored tasks do NOT auto-run).
    ingest_pipeline.bind_queue_processors()
    for project in project_registry.list_projects():
        queue = ingest_queue.get_queue(project["path"])
        await queue.restore()
        queue.start_worker()
    yield


app = FastAPI(title="py-llm-wiki", version="0.1.0", lifespan=lifespan)


# --- CORS: same allow-list as llm_wiki cors.rs local_cors_headers ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "tauri://localhost",
        "http://tauri.localhost",
        "null",  # sandboxed browser contexts (the clipper-style extensions)
    ],
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-LLM-Wiki-Token"],
    allow_credentials=False,
    max_age=600,
)


# --- Rate limit: 120 req/s sliding window (api_server.rs:35-36) ---
class RateLimiter:
    def __init__(self, max_requests: int, window_secs: float):
        self.max_requests = max_requests
        self.window = window_secs
        self.hits: deque[float] = deque()

    def allow(self) -> bool:
        now = time.monotonic()
        cutoff = now - self.window
        while self.hits and self.hits[0] < cutoff:
            self.hits.popleft()
        if len(self.hits) >= self.max_requests:
            return False
        self.hits.append(now)
        return True


_rate_limiter = RateLimiter(config.RATE_LIMIT_MAX_REQUESTS, 1.0)


@app.middleware("http")
async def rate_limit_and_body_caps(request: Request, call_next):
    path = request.url.path
    is_health = path in ("/health", f"{config.API_PREFIX}/health")
    if request.method != "OPTIONS" and not is_health:
        if not _rate_limiter.allow():
            return JSONResponse(
                status_code=429,
                content={"ok": False, "error": "Too many requests"},
            )
        if path.endswith("/chat"):
            limit = config.MAX_CHAT_BODY_BYTES
        elif path.endswith("/llm/proxy"):
            limit = router_llm.MAX_PROXY_BODY
        else:
            limit = config.MAX_BODY_BYTES
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > limit:
            return JSONResponse(
                status_code=413,
                content={"ok": False, "error": "Request body is too large"},
            )
    return await call_next(request)


# --- Error envelope: {ok:false,error} like api_server.rs err() ---
@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status_code, content=exc.detail)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": "Internal API server error"},
    )


@app.get("/health")
def bare_health(request: Request):
    """Always reachable, even when the API kill-switch is off."""
    return router_v1.health(request)


app.include_router(router_v1.router)
app.include_router(router_fs.router)
app.include_router(router_settings.router)
app.include_router(router_tauri.router)
app.include_router(router_llm.router)
app.include_router(router_ingest.router)

# 浏览器版 UI (Dockerfile 多阶段构建产物): 存在则同源托管
# API 路由注册在前, 不会被静态挂载遮蔽
from pathlib import Path as _Path

from fastapi.staticfiles import StaticFiles as _StaticFiles

_ui_dist = _Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _ui_dist.exists():
    app.mount("/", _StaticFiles(directory=str(_ui_dist), html=True), name="ui")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=config.HOST,
        port=config.PORT,
        log_level="info",
    )
