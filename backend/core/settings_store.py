"""app-state.json mirror of llm_wiki's Tauri Store persistence.

The desktop app keeps one JSON file that both the frontend (Tauri Store
plugin) and the Rust backend read. Here the FastAPI backend owns the
file; the frontend reads/writes it through GET/PUT /api/v1/settings.

Keys mirror llm_wiki (src/lib/project-store.ts): llmConfig,
providerConfigs, customLlmPresets, activePresetId, taskModelRouting,
projectLlmOverrides, searchApiConfig, embeddingConfig, multimodalConfig,
mineruConfig, outputLanguage, proxyConfig, scheduledImportConfig,
sourceWatchConfig, apiConfig, generalConfig, recentProjects, lastProject,
projectRegistry, language, theme, zoomLevel.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from backend import config

_lock = threading.Lock()
_cache: dict[str, Any] | None = None
_cache_loaded_at: float = 0.0
_CACHE_TTL_SECS = 5.0  # api_server.rs: APP_STATE_CACHE_TTL


def load() -> dict[str, Any] | None:
    """Read app-state.json. None when missing or unparseable.

    The 5-second cache matches api_server.rs load_app_state so hot-path
    auth checks do not re-read disk on every request.
    """
    global _cache, _cache_loaded_at
    now = time.monotonic()
    if _cache is not None and now - _cache_loaded_at < _CACHE_TTL_SECS:
        return _cache
    value = _read_from_disk()
    with _lock:
        _cache = value
        _cache_loaded_at = now
    return value


def _read_from_disk() -> dict[str, Any] | None:
    try:
        raw = config.APP_STATE_FILE.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save(state: dict[str, Any]) -> None:
    """Persist the full state dict (atomic temp+rename like the store plugin)."""
    config.ensure_data_dir()
    tmp = config.APP_STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(config.APP_STATE_FILE)
    invalidate_cache()


def update(mutator) -> dict[str, Any]:
    """Read-modify-write under the process lock."""
    with _lock:
        state = _read_from_disk() or {}
        mutator(state)
        _write_atomic(state)
        _cache = state
        _cache_loaded_at = time.monotonic()
        return state


def _write_atomic(state: dict[str, Any]) -> None:
    config.ensure_data_dir()
    tmp = config.APP_STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(config.APP_STATE_FILE)


def invalidate_cache() -> None:
    """Force the next load() to hit disk (mirrors apiServerReloadConfig)."""
    global _cache
    with _lock:
        _cache = None


def get_api_config() -> dict[str, Any]:
    state = load()
    if state is None:
        return {}
    cfg = state.get("apiConfig")
    return cfg if isinstance(cfg, dict) else {}
