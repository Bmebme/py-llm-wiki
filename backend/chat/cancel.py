"""Chat cancellation registry — mirrors the Rust AgentCancellationRegistry.

Keyed by (project_id, session_id); the agent polls the event between
chunks and ends the stream with a terminal `cancelled` frame.
"""

from __future__ import annotations

import asyncio

_registry: dict[tuple[str, str], asyncio.Event] = {}


def request_cancel(project_id: str, session_id: str) -> bool:
    event = _registry.get((project_id, session_id))
    if event is None:
        return False
    event.set()
    return True


def register(project_id: str, session_id: str) -> asyncio.Event:
    """Return the existing event when one is already registered — an
    externally-set cancel signal must survive the agent's registration."""
    event = _registry.get((project_id, session_id))
    if event is not None:
        return event
    event = asyncio.Event()
    _registry[(project_id, session_id)] = event
    return event


def unregister(project_id: str, session_id: str) -> None:
    _registry.pop((project_id, session_id), None)


def is_cancelled(event: asyncio.Event) -> bool:
    return event.is_set()
