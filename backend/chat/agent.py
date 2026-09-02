"""Single-round chat agent — v0 port of the Rust agent runtime's chat
path (src-tauri/src/agent/runtime.rs), without the tool loop.

Emits the same AgentEvent shapes the desktop streams (agentStart,
turnStart, toolStart, toolEnd, referenceAdded, messageDelta, error,
done) so both the ported frontend and the mcp-server consume the stream
unchanged. Retrieval runs once via the hybrid search engine; mode
"deep" simply raises topK.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from backend.chat import cancel, context, session as session_store
from backend.chat.context import build_chat_system_prompt, build_chat_user_content
from backend.core import settings_store
from backend.core.context_budget import compute_context_budget
from backend.core.file_service import FsError, read_text
from backend.llm.client import RequestCancelled, stream_chat
from backend.llm.providers import get_provider_config
from backend.language.output import build_language_directive
from backend.search.engine import search_project_inner

TOP_K_NORMAL = 5
TOP_K_DEEP = 8

# Cap the final answer size so the SSE stream has a sane ceiling.
MAX_ANSWER_CHARS = 100_000


class AgentRequest:
    def __init__(self, body: dict):
        self.message = body.get("message")
        self.session_id = body.get("sessionId") or f"api_{uuid.uuid4().hex}"
        self.run_id = body.get("runId") or f"run_{uuid.uuid4().hex}"
        self.mode = body.get("mode") or "standard"
        self.retrieval_mode = body.get("retrievalMode") or "standard"
        self.top_k = body.get("topK") or (TOP_K_DEEP if self.mode == "deep" else TOP_K_NORMAL)
        self.include_content = body.get("includeContent", self.mode == "deep")
        self.history = body.get("history")
        self.history_explicit = body.get("historyExplicit") is True
        self.persist_session = body.get("persistSession", True)
        self.images = body.get("images") or []

    @classmethod
    def from_body(cls, body: dict) -> "AgentRequest":
        if not isinstance(body.get("message"), str) or not body["message"].strip():
            raise FsError("message is required")
        return cls(body)


def _agent_event(event_type: str, **fields) -> dict:
    return {"type": event_type, **fields}


def _llm_config() -> dict:
    state = settings_store.load() or {}
    cfg = state.get("llmConfig")
    if not isinstance(cfg, dict) or not cfg.get("provider"):
        raise FsError("No LLM provider configured — set one in Settings before chatting.")
    return cfg


class ChatAgent:
    def __init__(self, project: dict, http_client=None):
        self.project = project
        self.http_client = http_client

    def _read_optional(self, rel: str) -> str:
        try:
            return read_text(Path(self.project["path"]) / rel)
        except FsError:
            return ""

    async def run(
        self,
        request: AgentRequest,
        events: asyncio.Queue | None = None,
    ) -> dict:
        """Returns the aggregate done-frame payload. Events are pushed to
        `events` (the SSE router drains it) or collected internally."""
        if events is None:
            events = asyncio.Queue()

        cancel_event = cancel.register(self.project["id"], request.session_id)
        try:
            return await self._run_inner(request, events, cancel_event)
        finally:
            cancel.unregister(self.project["id"], request.session_id)

    async def _run_inner(
        self, request: AgentRequest, events: asyncio.Queue, cancel_event: asyncio.Event
    ) -> dict:
        project_path = self.project["path"]
        await events.put(_agent_event("agentStart", sessionId=request.session_id))
        await events.put(_agent_event("turnStart", mode=request.mode))

        # History: explicit when provided; otherwise last 12 from the
        # persisted session (runtime.rs auto-hydration).
        history = request.history
        if not history and not request.history_explicit:
            session = session_store.load_session(project_path, request.session_id)
            history = session_store.recent_messages(session, 12)

        # ── Retrieval ────────────────────────────────────────────────
        await events.put(_agent_event("toolStart", tool="wiki.search", input=request.message))
        top_k = max(1, min(int(request.top_k), 50))
        search_response = search_project_inner(
            project_path, request.message, top_k, include_content=True
        )
        pages = search_response["results"]
        tool_detail = (
            f"{len(pages)} wiki pages retrieved for \"{request.message[:120]}\""
        )
        await events.put(_agent_event("toolEnd", tool="wiki.search", output=tool_detail))

        references = []
        for page in pages:
            reference = {
                "title": page.get("title") or page["path"],
                "path": page["path"],
                "kind": "wiki",
                "snippet": page.get("snippet", "")[:500],
            }
            references.append(reference)
            await events.put(_agent_event("referenceAdded", reference=reference))

        # ── Context assembly ─────────────────────────────────────────
        purpose = self._read_optional("purpose.md")
        schema = self._read_optional("schema.md")
        index = self._read_optional("wiki/index.md")

        config = _llm_config()
        provider_config = get_provider_config(config)
        max_ctx = config.get("maxContextSize")
        budget = compute_context_budget(max_ctx)
        language_directive = build_language_directive(
            (settings_store.load() or {}).get("outputLanguage"), request.message
        )
        system_prompt = build_chat_system_prompt(
            purpose, schema, index, language_directive, max_ctx
        )
        user_content = build_chat_user_content(
            request.message, pages, history,
            int(budget["pageBudget"]), int(budget["maxPageSize"]),
        )

        # ── Stream the answer ────────────────────────────────────────
        answer_parts: list[str] = []
        prompt_chars = len(system_prompt) + len(user_content)
        completion_chars = 0
        cancelled = False

        async def _check_cancel() -> None:
            nonlocal cancelled
            if cancel_event.is_set():
                cancelled = True
                raise RequestCancelled()

        try:
            async for event in stream_chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                config,
                provider_config,
                client=self.http_client,
            ):
                await _check_cancel()
                if event["type"] == "delta":
                    completion_chars += len(event["text"])
                    if completion_chars <= MAX_ANSWER_CHARS:
                        answer_parts.append(event["text"])
                        await events.put(
                            _agent_event("messageDelta", text=event["text"])
                        )
                # reasoning events are intentionally not forwarded in v0
                # (the UI renders them via the thinking channel — M4).
        except RequestCancelled:
            cancelled = True

        if cancelled:
            await events.put(
                _agent_event(
                    "error",
                    message="Agent turn cancelled",
                )
            )
            raise ChatCancelled()

        answer = "".join(answer_parts)
        if not answer.strip():
            await events.put(_agent_event("error", message="Model produced no answer content"))
            raise FsError("Model produced no answer content")

        await events.put(_agent_event("done", sessionId=request.session_id))

        # ── Persistence ──────────────────────────────────────────────
        if request.persist_session:
            session_store.append_turn(
                project_path, request.session_id, request.message, answer
            )

        return {
            "projectId": self.project["id"],
            "sessionId": request.session_id,
            "mode": request.mode,
            "message": {"role": "assistant", "content": answer},
            "references": references,
            "toolEvents": [
                {"tool": "wiki.search", "status": "completed", "detail": tool_detail}
            ],
            "events": [],
            "usage": {
                "promptChars": prompt_chars,
                "completionChars": completion_chars,
                "referenceCount": len(references),
                "toolEventCount": 1,
            },
        }


class ChatCancelled(Exception):
    """User cancelled the turn — terminal `cancelled` frame."""
