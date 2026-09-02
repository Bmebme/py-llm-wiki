"""Chat agent + SSE stream tests — the M2 acceptance suite.

Covers: SSE frame order (meta → agentStart → turnStart → toolStart →
toolEnd → referenceAdded → messageDelta → done), the done aggregate
shape, JSON mode, cancel behavior, session persistence, and the raw
SSE line parser via httpx.MockTransport.
"""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.core import project as project_core
from backend.core import settings_store
from backend.main import app

client = TestClient(app)

OPENAI_SSE_LINES = (
    b'data: {"choices":[{"delta":{"content":"The wiki"}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":" says attention."}}]}\n\n'
    b"data: [DONE]\n\n"
)

MOCK_ANSWER = "The wiki says attention."


@pytest.fixture()
def wiki_project(tmp_path, monkeypatch):
    settings_store.save({
        "llmConfig": {
            "provider": "openai",
            "apiKey": "sk-mock",
            "model": "gpt-4o",
            "streamingEnabled": True,
            "maxContextSize": 204800,
        }
    })

    async def fake_stream_chat(
        messages, config, provider_config, overrides=None,
        cancel_signal=None, client=None, **kwargs,
    ):
        for text in ("The wiki", " says attention."):
            yield {"type": "delta", "text": text}

    monkeypatch.setattr("backend.chat.agent.stream_chat", fake_stream_chat)

    project = project_core.create_project("chat-demo", str(tmp_path))
    project_core.ensure_project_id(project.path)
    from backend.core import project_registry

    project_registry.register(project.id, project.name, project.path)
    (tmp_path / "chat-demo" / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "chat-demo" / "wiki" / "concepts" / "attention.md").write_text(
        "---\ntype: concept\ntitle: Attention\n---\n\n# Attention\n\n"
        "Attention computes weighted sums of values.",
        encoding="utf-8",
    )
    return project


def _parse_sse_frames(lines: list[str]) -> list[tuple[str, dict]]:
    frames: list[tuple[str, dict]] = []
    event_name = None
    for line in lines:
        if line.startswith("event: "):
            event_name = line[7:]
        elif line.startswith("data: ") and event_name:
            frames.append((event_name, json.loads(line[6:])))
            event_name = None
    return frames


def test_chat_sse_frame_order_and_aggregate(wiki_project):
    with client.stream(
        "POST",
        "/api/v1/projects/current/chat",
        json={"message": "what is attention?", "stream": True, "sessionId": "test-sess-1"},
    ) as response:
        assert response.status_code == 200
        lines = list(response.iter_lines())

    frames = _parse_sse_frames(lines)
    events = [name for name, _ in frames]
    assert events[0] == "meta"
    meta = frames[0][1]
    assert meta["projectId"] == wiki_project.id
    assert meta["sessionId"] == "test-sess-1"

    agent_types = [data["type"] for name, data in frames if name == "agent"]
    assert agent_types[0] == "agentStart"
    assert "turnStart" in agent_types
    assert "toolStart" in agent_types
    assert "toolEnd" in agent_types
    assert "referenceAdded" in agent_types
    assert "messageDelta" in agent_types
    assert agent_types[-1] == "done"

    done_name, done = frames[-1]
    assert done_name == "done"
    assert done["ok"] is True
    assert done["message"]["content"] == MOCK_ANSWER
    assert len(done["references"]) >= 1
    assert done["usage"]["referenceCount"] == len(done["references"])


def test_chat_json_mode(wiki_project):
    response = client.post(
        "/api/v1/projects/current/chat",
        json={"message": "what is attention?", "sessionId": "test-sess-2"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["message"]["content"] == MOCK_ANSWER


def test_chat_requires_message(wiki_project):
    response = client.post(
        "/api/v1/projects/current/chat", json={"message": "  ", "stream": True}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "message is required"


def test_cancel_without_active_turn_returns_404(wiki_project):
    response = client.post("/api/v1/projects/current/chat/nope/cancel")
    assert response.status_code == 404


def test_agent_cancel_mid_stream(wiki_project):
    """Unit-level cancel: the agent aborts when the cancel event fires."""
    from backend.chat.agent import AgentRequest, ChatAgent, ChatCancelled
    from backend.chat.cancel import register

    async def run():
        request = AgentRequest.from_body({"message": "hi", "sessionId": "cancel-sess"})
        agent = ChatAgent({"id": wiki_project.id, "name": "x", "path": wiki_project.path})
        register(wiki_project.id, "cancel-sess").set()  # pre-cancelled
        with pytest.raises(ChatCancelled):
            await agent.run(request)

    asyncio.run(run())


def test_session_persistence(wiki_project):
    from backend.chat import session as session_store

    client.post(
        "/api/v1/projects/current/chat",
        json={"message": "what is attention?", "sessionId": "persist-sess"},
    )
    session = session_store.load_session(wiki_project.path, "persist-sess")
    messages = session["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["content"] == MOCK_ANSWER
    assert session_store.recent_messages(session, 12)[0]["role"] == "user"


def test_stream_chat_sse_parser_with_mock_transport():
    """Direct client-level test of the real SSE line parser."""
    from backend.llm.client import stream_chat
    from backend.llm.providers import get_provider_config

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=OPENAI_SSE_LINES,
        )
    )
    config = {
        "provider": "openai",
        "apiKey": "sk",
        "model": "gpt-4o",
        "streamingEnabled": True,
    }
    provider_config = get_provider_config(config)

    async def run():
        async with httpx.AsyncClient(transport=transport) as http_client:
            events = [
                event
                async for event in stream_chat(
                    [{"role": "user", "content": "hi"}],
                    config,
                    provider_config,
                    client=http_client,
                )
            ]
        return events

    events = asyncio.run(run())
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert "".join(deltas) == MOCK_ANSWER
