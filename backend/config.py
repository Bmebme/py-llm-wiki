"""Server-level configuration.

Mirrors the constants in llm_wiki's src-tauri/src/api_server.rs so the
HTTP surface behaves identically for external clients (mcp-server etc.).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# --- Network ---
PORT: int = int(os.environ.get("PY_LLM_WIKI_PORT", "19828"))
HOST: str = os.environ.get("PY_LLM_WIKI_HOST", "127.0.0.1")

# --- HTTP API limits (api_server.rs:22-40) ---
API_PREFIX = "/api/v1"
MAX_BODY_BYTES = 1024 * 1024
MAX_CHAT_BODY_BYTES = 40 * 1024 * 1024
MAX_FILE_CONTENT_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_FILES = 2_000
HARD_MAX_FILES = 10_000
DEFAULT_MAX_REVIEWS = 200
HARD_MAX_REVIEWS = 1_000
MAX_SEARCH_RESULTS = 50
RATE_LIMIT_MAX_REQUESTS = 300  # per 1-second window（浏览器版 UI 挂载风暴的余量）
MAX_IN_FLIGHT_CHAT_STREAMS = 8
SSE_HEARTBEAT_INTERVAL = 10.0

# --- Data directory (replaces the Tauri app-data dir) ---
DATA_DIR = Path(
    os.environ.get("PY_LLM_WIKI_DATA_DIR", "~/.py-llm-wiki")
).expanduser()

APP_STATE_FILE = DATA_DIR / "app-state.json"


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def env_llm_override() -> dict[str, Any] | None:
    """统一 LLM 约定 (crucible deploy/.env): LLM_WIKI_LLM_* 覆盖 llmConfig。

    容器化部署时由环境注入 LLM 配置, 免手工改 app-state.json。
    任一变量未设置则返回 None (不覆盖)。
    """
    base = os.environ.get("LLM_WIKI_LLM_BASE")
    key = os.environ.get("LLM_WIKI_LLM_API_KEY")
    model = os.environ.get("LLM_WIKI_LLM_MODEL")
    if not any((base, key, model)):
        return None
    override: dict[str, Any] = {"provider": "custom", "apiMode": "chat_completions"}
    if base:
        override["customEndpoint"] = base
    if key:
        override["apiKey"] = key
    if model:
        override["model"] = model
    return override
