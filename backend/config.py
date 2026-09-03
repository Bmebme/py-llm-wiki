"""Server-level configuration.

Mirrors the constants in llm_wiki's src-tauri/src/api_server.rs so the
HTTP surface behaves identically for external clients (mcp-server etc.).
"""

from __future__ import annotations

import os
from pathlib import Path

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
