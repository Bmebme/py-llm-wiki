"""Token auth — port of api_server.rs authorization (545-684)."""

from __future__ import annotations

import hmac
import os

from fastapi import Request

from backend import config
from backend.core import settings_store


def api_token() -> str | None:
    """Env LLM_WIKI_API_TOKEN overrides apiConfig.token in app-state."""
    env_token = os.environ.get("LLM_WIKI_API_TOKEN", "").strip()
    if env_token:
        return env_token
    cfg = settings_store.get_api_config()
    token = cfg.get("token")
    return token.strip() if isinstance(token, str) and token.strip() else None


def token_source() -> str:
    if os.environ.get("LLM_WIKI_API_TOKEN", "").strip():
        return "env"
    cfg = settings_store.get_api_config()
    token = cfg.get("token")
    if isinstance(token, str) and token.strip():
        return "store"
    return "none"


def api_enabled() -> bool:
    """Non-health endpoints 503 when disabled. Defaults true (api_server.rs:653)."""
    cfg = settings_store.get_api_config()
    return bool(cfg.get("enabled", True))


def api_allow_unauthenticated() -> bool:
    """Explicit apiConfig wins; otherwise default to open when bound to
    loopback (single-user localhost-first v0 decision) and fail-closed
    when bound to a network interface (matches desktop behavior)."""
    cfg = settings_store.get_api_config()
    if "allowUnauthenticated" in cfg:
        return bool(cfg["allowUnauthenticated"])
    return config.HOST in ("127.0.0.1", "localhost")


def api_allow_lan_access() -> bool:
    cfg = settings_store.get_api_config()
    return bool(cfg.get("allowLanAccess", False))


def api_mcp_enabled() -> bool:
    cfg = settings_store.get_api_config()
    return bool(cfg.get("mcpEnabled", False))


def auth_required() -> bool:
    return not api_allow_unauthenticated()


def constant_time_eq(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def is_token_authorized(query_token: str | None, headers) -> bool:
    token = api_token()
    if token is None:
        return False
    if query_token is not None and constant_time_eq(query_token, token):
        return True
    for key, value in headers.items():
        if key.lower() == "x-llm-wiki-token":
            if constant_time_eq(value, token):
                return True
        if key.lower() == "authorization":
            if value.startswith("Bearer ") and constant_time_eq(value[7:], token):
                return True
    return False


def is_authorized(request: Request) -> bool:
    """Port of is_authorized (api_server.rs:545-550)."""
    if not auth_required():
        return True
    return is_token_authorized(request.query_params.get("token"), request.headers)
