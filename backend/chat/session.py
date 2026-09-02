"""Agent session persistence — .llm-wiki/agent-sessions/{id}.json.

Mirrors the Rust AgentSessionStore: sanitized session ids, last-12
history hydration, capped cache.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

MAX_CACHED_SESSIONS = 200


def sanitize_session_id(session_id: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", session_id)[:128]
    return cleaned or "default"


def _sessions_dir(project_path: str) -> Path:
    return Path(project_path) / ".llm-wiki" / "agent-sessions"


def _session_path(project_path: str, session_id: str) -> Path:
    return _sessions_dir(project_path) / f"{sanitize_session_id(session_id)}.json"


def load_session(project_path: str, session_id: str) -> dict:
    try:
        raw = json.loads(_session_path(project_path, session_id).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except (OSError, json.JSONDecodeError):
        pass
    return {"id": sanitize_session_id(session_id), "messages": []}


def recent_messages(session: dict, limit: int = 12) -> list[dict]:
    messages = session.get("messages")
    if not isinstance(messages, list):
        return []
    return [
        {"role": m.get("role"), "content": m.get("content")}
        for m in messages[-limit:]
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]


def append_turn(project_path: str, session_id: str, user_message: str, assistant_message: str) -> None:
    path = _session_path(project_path, session_id)
    session = load_session(project_path, session_id)
    messages = session.get("messages")
    if not isinstance(messages, list):
        messages = []
    now = int(time.time() * 1000)
    messages.extend([
        {"role": "user", "content": user_message, "ts": now},
        {"role": "assistant", "content": assistant_message, "ts": now},
    ])
    # Cap at 100 messages (mirrors the chat persistence cap).
    session["messages"] = messages[-100:]
    session["updatedAt"] = now
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def list_sessions(project_path: str) -> list[dict]:
    directory = _sessions_dir(project_path)
    if not directory.exists():
        return []
    out: list[dict] = []
    for entry in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(entry.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("id"):
                out.append({"id": raw["id"], "updatedAt": raw.get("updatedAt", 0)})
        except (OSError, json.JSONDecodeError):
            continue
    return out[:MAX_CACHED_SESSIONS]
