"""Async streaming LLM client.

Port of llm_wiki's ``src/lib/llm-client.ts`` (HTTP providers) plus the
reasoning-line helpers from ``src/lib/reasoning-detector.ts``.

``stream_chat`` is an async generator yielding events:

    {"type": "delta", "text": str}
    {"type": "reasoning", "text": str}

Errors surface as exceptions raised from the generator:

- ``LlmError`` (a ``backend.core.file_service.FsError``) for user-facing
  errors: HTTP status, JSON endpoint error envelopes, empty responses,
  the reasoning-only diagnostic, network failures.
- ``RequestCancelled`` when the caller's ``asyncio.Event``-style cancel
  signal fires (maps to llm-client.ts's silent cancel / onDone).
- ``RequestTimeout`` when the long-horizon backstop fires
  (``requestTimeoutMinutes``, default 30, clamped 1..1440).

The HTTP transport is injectable: pass ``client=httpx.AsyncClient(...)``
(tests use ``httpx.MockTransport``); when omitted a client is created
and closed for the duration of the call.
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import json
import os
import re
from typing import Any, AsyncIterator, Callable, Optional

import httpx

from backend.core.file_service import FsError
from backend.llm.providers import is_azure_openai_endpoint

# llm-client.ts:135-138
_REQUEST_CANCELLED_RE = re.compile(r"^request cancel(?:l)?ed$", re.IGNORECASE)
# llm-client.ts:140-143
_REASONING_ONLY_RE = re.compile(
    r"^Model produced [\d,]+ characters of reasoning / chain-of-thought, but no actual response content\."
)

# llm-client.ts:408
REASONING_DIAGNOSTIC_THRESHOLD = 200

# reasoning-detector.ts:39-40
REASONING_FIELD_RE = re.compile(r'"reasoning(?:_content)?"\s*:\s*"((?:[^"\\]|\\.)*)"')


class LlmError(FsError):
    """User-facing LLM request error (subclass of FsError)."""


class RequestCancelled(Exception):
    """User-initiated cancel. Maps to llm-client.ts's silent onDone."""


class RequestTimeout(Exception):
    """The long-horizon backstop fired. Message matches the TS timeout text."""


def is_request_cancelled_error(err: Any) -> bool:
    """Port of isRequestCancelledError (llm-client.ts:135-138)."""
    message = err.message if isinstance(err, BaseException) else str(err)
    return _REQUEST_CANCELLED_RE.match(message.strip()) is not None


def is_reasoning_only_response_error(err: Any) -> bool:
    """Port of isReasoningOnlyResponseError (llm-client.ts:140-143)."""
    message = err.message if isinstance(err, BaseException) else str(err)
    return _REASONING_ONLY_RE.match(message) is not None


# --- Reasoning line helpers (reasoning-detector.ts) ------------------------

def count_reasoning_chars_in_line(raw_line: str) -> int:
    """Count JSON-escaped reasoning-field text on a raw SSE line.

    Counts the escaped form's length (e.g. ``\\n`` counts as 2), matching
    the TS implementation — close enough for the 200-char threshold.
    """
    return sum(len(m) for m in REASONING_FIELD_RE.findall(raw_line))


def extract_reasoning_text_from_line(raw_line: str) -> list[str]:
    """Port of extractReasoningTextFromLine (reasoning-detector.ts:50-87).

    Handles OpenAI-style ``delta.reasoning_content`` / ``delta.reasoning``,
    Anthropic ``delta.type == "thinking_delta"`` (``thinking`` + ``text``),
    and Gemini ``thought: true`` parts.
    """
    line = raw_line.strip()
    if not line.startswith("data: "):
        return []
    data = line[6:].strip()
    if not data or data == "[DONE]":
        return []
    try:
        parsed = json.loads(data)
    except Exception:
        return []

    out: list[str] = []
    for choice in parsed.get("choices") or []:
        delta = choice.get("delta") if isinstance(choice, dict) else None
        if isinstance(delta, dict):
            if isinstance(delta.get("reasoning_content"), str):
                out.append(delta["reasoning_content"])
            if isinstance(delta.get("reasoning"), str):
                out.append(delta["reasoning"])

    delta = parsed.get("delta")
    if isinstance(delta, dict) and delta.get("type") == "thinking_delta":
        if isinstance(delta.get("thinking"), str):
            out.append(delta["thinking"])
        if isinstance(delta.get("text"), str):
            out.append(delta["text"])

    for candidate in parsed.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        for part in content.get("parts") or []:
            if isinstance(part, dict) and part.get("thought") and isinstance(part.get("text"), str):
                out.append(part["text"])

    return out


# --- SSE framing helpers (llm-client.ts) -----------------------------------

class _Utf8Decoder:
    """Incremental UTF-8 decoder mirroring TextDecoder({stream: true})."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")()

    def decode_stream(self, chunk: bytes) -> str:
        return self._decoder.decode(chunk, final=False)

    def flush(self) -> str:
        return self._decoder.decode(b"", final=True)


def parse_lines(decoder: _Utf8Decoder, chunk: bytes, buffer: str) -> tuple[list[str], str]:
    """Port of parseLines (llm-client.ts:56-65)."""
    text = buffer + decoder.decode_stream(chunk)
    lines = text.split("\n")
    remaining = lines.pop() if lines else ""
    return lines, remaining


def parse_endpoint_error_envelope(record: str) -> Optional[str]:
    """Port of parseEndpointErrorEnvelope (llm-client.ts:74-95).

    Returns the error message string (``LLM endpoint error...``) or None.
    """
    payload = record[5:].strip() if record.startswith("data:") else record
    if not payload.startswith("{"):
        return None
    try:
        parsed = json.loads(payload)
    except Exception:
        return None
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(error, str):
        message: Any = error
    elif isinstance(error, dict):
        message = error.get("message")
    else:
        message = None
    if not message:
        return None
    code = ""
    if isinstance(error, dict) and error.get("code") is not None:
        code = f" {error['code']}"
    return f"LLM endpoint error{code}: {message}"


def split_final_stream_records(text: str) -> list[str]:
    """Port of splitFinalStreamRecords (llm-client.ts:97-133).

    Handles fully buffered SSE bodies with escaped record separators
    (``\\n\\n`` literal backslashes). Only splits after a complete JSON
    SSE record so separator-like text inside a JSON string is preserved.
    """
    if re.search(r"[\r\n]", text) or not re.match(r"^\s*data:", text):
        return re.split(r"\r?\n", text)

    records: list[str] = []
    separator = re.compile(r"(?:\\r)?\\n(?:\\r)?\\n(?=data:)")
    record_start = 0
    for match in separator.finditer(text):
        candidate = text[record_start : match.start()].strip()
        payload = candidate[5:].strip() if candidate.startswith("data:") else ""
        complete = payload == "[DONE]"
        if not complete and payload.startswith("{"):
            try:
                json.loads(payload)
                complete = True
            except Exception:
                # Separator-like text inside an incomplete JSON string.
                pass
        if complete:
            records.append(candidate)
            record_start = match.start() + len(match.group(0))

    records.append(text[record_start:])
    return records


# --- Cancellation / timeout plumbing ---------------------------------------

async def _watchdog(cancel_signal: Optional[asyncio.Event], timeout_fired: asyncio.Event, timeout_seconds: float) -> None:
    """Long-horizon backstop. If the user's cancel signal fires first, no
    timeout; otherwise set ``timeout_fired``. Mirrors the combined abort in
    llm-client.ts:199-213."""
    if cancel_signal is not None:
        try:
            await asyncio.wait_for(cancel_signal.wait(), timeout=timeout_seconds)
            return
        except asyncio.TimeoutError:
            pass
    else:
        await asyncio.sleep(timeout_seconds)
    timeout_fired.set()


async def _await_with_control(
    coro: Any,
    cancel_signal: Optional[asyncio.Event],
    timeout_fired: Optional[asyncio.Event],
    timeout_factory: Callable[[], RequestTimeout],
) -> Any:
    """Await ``coro``, racing it against the cancel/timeout events.

    When the cancel event fires first -> RequestCancelled; when the
    timeout event fires first -> the timeout error. Mirrors the abort
    mapping in llm-client.ts (timeout takes precedence when both fired).
    """
    if cancel_signal is None and timeout_fired is None:
        return await coro
    task = asyncio.ensure_future(coro)
    waiters: list[tuple[asyncio.Future, str]] = []
    if cancel_signal is not None:
        waiters.append((asyncio.ensure_future(cancel_signal.wait()), "cancel"))
    if timeout_fired is not None:
        waiters.append((asyncio.ensure_future(timeout_fired.wait()), "timeout"))
    try:
        done, pending = await asyncio.wait(
            {task, *(t for t, _ in waiters)}, return_when=asyncio.FIRST_COMPLETED
        )
    except BaseException:
        task.cancel()
        for t, _ in waiters:
            t.cancel()
        raise
    for t, kind in waiters:
        if t in done and t.result() is True:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
            if kind == "cancel":
                raise RequestCancelled()
            raise timeout_factory()
    for t, _ in waiters:
        if t not in done:
            t.cancel()
    return task.result()


class _StreamEntry:
    """Races ``async with`` entry (the HTTP handshake) against cancel."""

    def __init__(
        self,
        cm: Any,
        cancel_signal: Optional[asyncio.Event],
        timeout_fired: Optional[asyncio.Event],
        timeout_factory: Callable[[], RequestTimeout],
    ) -> None:
        self._cm = cm
        self._cancel_signal = cancel_signal
        self._timeout_fired = timeout_fired
        self._timeout_factory = timeout_factory

    async def __aenter__(self) -> httpx.Response:
        return await _await_with_control(
            self._cm.__aenter__(),
            self._cancel_signal,
            self._timeout_fired,
            self._timeout_factory,
        )

    async def __aexit__(self, *exc_info: Any) -> None:
        return await self._cm.__aexit__(*exc_info)


def _is_network_error(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.HTTPError, httpx.InvalidURL))


# --- Request helpers -------------------------------------------------------

async def _check_http_response(response: httpx.Response, config: dict[str, Any]) -> None:
    """Raise LlmError for non-2xx responses (llm-client.ts:256-281)."""
    if 200 <= response.status_code < 300:
        return
    error_detail = f"HTTP {response.status_code}: {response.reason_phrase}"
    try:
        raw = await response.aread()
        body = raw.decode("utf-8", "replace") if raw else ""
        if body:
            error_detail += f" — {body}"
    except Exception:
        # Ignore body read failure (TS catches and ignores too).
        pass
    if (
        response.status_code == 404
        and (
            config.get("provider") == "azure"
            or (
                config.get("provider") == "custom"
                and is_azure_openai_endpoint(config.get("customEndpoint") or "")
            )
        )
    ):
        raise LlmError(
            f"{error_detail} — Azure 404 usually means the deployment name is wrong. "
            "Set Model to your Azure deployment name (not the model SKU), "
            "and Endpoint to https://<resource>.openai.azure.com "
            "or .../openai/deployments/<deployment-name>."
        )
    raise LlmError(error_detail)


def _default_client() -> httpx.AsyncClient:
    # read=None: the long-horizon watchdog owns the read timeout.
    # LLM_WIKI_NO_PROXY=1 时禁用系统代理（公司网络/代理环境下 LLM 直连，避免 504）
    trust_env = os.environ.get("LLM_WIKI_NO_PROXY", "").strip().lower() not in ("1", "true", "yes")
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0),
        trust_env=trust_env,
    )


def _timeout_factory_for(minutes: int) -> Callable[[], RequestTimeout]:
    return lambda: RequestTimeout(
        f"Request timed out after {minutes} min. Try a faster model or a smaller context."
    )


# --- Line processing -------------------------------------------------------

def _process_line(
    line: str,
    provider_config: dict[str, Any],
    state: dict[str, int],
) -> tuple[Optional[str], list[tuple[str, str]]]:
    """Process one SSE line. Returns (endpoint_error, events) where each
    event is (kind, text) with kind in {"delta", "reasoning"}.

    Mirrors processRecord in llm-client.ts:349-361: reasoning is counted
    and emitted before the content token of the same line.
    """
    trimmed = line.strip()
    if not trimmed:
        return None, []
    state["reasoning"] += count_reasoning_chars_in_line(trimmed)
    events: list[tuple[str, str]] = [
        ("reasoning", part) for part in extract_reasoning_text_from_line(trimmed)
    ]
    token = provider_config["parse_stream"](trimmed)
    if token is not None:
        state["content"] += len(token)
        events.append(("delta", token))
        return None, events
    error = parse_endpoint_error_envelope(trimmed)
    if error:
        return error, events
    return None, events


# --- Public API ------------------------------------------------------------

async def stream_chat(
    messages: list[dict[str, Any]],
    config: dict[str, Any],
    provider_config: dict[str, Any],
    overrides: Optional[dict[str, Any]] = None,
    cancel_signal: Optional[asyncio.Event] = None,
    client: Optional[httpx.AsyncClient] = None,
    *,
    _timeout_seconds: Optional[float] = None,
) -> AsyncIterator[dict[str, str]]:
    """Stream a chat completion, yielding delta/reasoning events.

    ``client`` is an optional httpx.AsyncClient (injectable for tests).
    ``_timeout_seconds`` overrides the watchdog duration for tests.
    """
    if cancel_signal is not None and cancel_signal.is_set():
        raise RequestCancelled()

    timeout_minutes = max(1, min(1440, config.get("requestTimeoutMinutes") or 30))
    timeout_seconds = (
        _timeout_seconds if _timeout_seconds is not None else timeout_minutes * 60
    )
    timeout_fired = asyncio.Event()
    timeout_factory = _timeout_factory_for(timeout_minutes)
    watchdog = asyncio.ensure_future(_watchdog(cancel_signal, timeout_fired, timeout_seconds))

    close_client = client is None
    http = client if client is not None else _default_client()

    try:
        body = provider_config["build_body"](messages, overrides)
        body_bytes = json.dumps(body, ensure_ascii=True).encode("utf-8")
        entry = _StreamEntry(
            http.stream(
                "POST",
                provider_config["url"],
                headers=provider_config["headers"],
                content=body_bytes,
            ),
            cancel_signal,
            timeout_fired,
            timeout_factory,
        )
        try:
            response = await entry.__aenter__()
        except httpx.HTTPError as exc:
            if timeout_fired.is_set():
                raise timeout_factory() from exc
            if cancel_signal is not None and cancel_signal.is_set():
                raise RequestCancelled() from exc
            raise LlmError(
                f"Network error reaching {provider_config['url']}. "
                "Check endpoint URL, API key, and connectivity."
            ) from exc
        except (RequestCancelled, RequestTimeout):
            raise

        try:
            await _check_http_response(response, config)

            if not provider_config["streaming"]:
                # Non-streaming wire: parse the complete JSON response.
                try:
                    raw = await _await_with_control(
                        response.aread(), cancel_signal, timeout_fired, timeout_factory
                    )
                except httpx.HTTPError as exc:
                    if timeout_fired.is_set():
                        raise timeout_factory() from exc
                    if cancel_signal is not None and cancel_signal.is_set():
                        raise RequestCancelled() from exc
                    raise LlmError(
                        "Connection lost while reading the complete response. Try again."
                    ) from exc
                try:
                    payload = json.loads(raw.decode("utf-8", "replace"))
                except Exception as exc:
                    raise LlmError("Model returned an unparseable response") from exc
                content = provider_config["parse_response"](payload)
                if not content:
                    raise LlmError("Model returned an empty non-streaming response")
                yield {"type": "delta", "text": content}
                return

            # Streaming wire: parse the SSE body.
            iterator = response.aiter_bytes()
            decoder = _Utf8Decoder()
            line_buffer = ""
            state = {"content": 0, "reasoning": 0}

            while True:
                try:
                    chunk = await _await_with_control(
                        iterator.__anext__(), cancel_signal, timeout_fired, timeout_factory
                    )
                except StopAsyncIteration:
                    break
                except httpx.HTTPError as exc:
                    if timeout_fired.is_set():
                        raise timeout_factory() from exc
                    if cancel_signal is not None and cancel_signal.is_set():
                        raise RequestCancelled() from exc
                    raise LlmError("Connection lost during streaming. Try again.") from exc
                lines, line_buffer = parse_lines(decoder, chunk, line_buffer)
                for line in lines:
                    error, events = _process_line(line, provider_config, state)
                    for kind, text in events:
                        yield {"type": kind, "text": text}
                    if error:
                        raise LlmError(error)

            # EOF: flush the decoder and process the tail records.
            final_text = line_buffer + decoder.flush()
            for line in split_final_stream_records(final_text):
                error, events = _process_line(line, provider_config, state)
                for kind, text in events:
                    yield {"type": kind, "text": text}
                if error:
                    raise LlmError(error)

            # Reasoning-only diagnostic (llm-client.ts:407-422).
            if state["content"] == 0 and state["reasoning"] >= REASONING_DIAGNOSTIC_THRESHOLD:
                raise LlmError(
                    f"Model produced {state['reasoning']:,} characters of reasoning / "
                    "chain-of-thought, but no actual response content. This usually means "
                    "the endpoint hit a thinking-token limit, the model didn't transition "
                    "from thinking to answering, or the endpoint is misbehaving (the "
                    "official Anthropic / OpenAI APIs don't have this issue). Try a shorter "
                    "input, increase max_tokens, or switch to a different model in Settings."
                )
        finally:
            await entry.__aexit__(None, None, None)
    finally:
        watchdog.cancel()
        with contextlib.suppress(BaseException):
            await watchdog
        if close_client:
            await http.aclose()


async def generate_text(
    messages: list[dict[str, Any]],
    config: dict[str, Any],
    provider_config: dict[str, Any],
    max_tokens: Optional[int] = None,
    overrides: Optional[dict[str, Any]] = None,
    cancel_signal: Optional[asyncio.Event] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """Non-streaming fallback: POST with ``stream: false`` and parse the
    complete response via the provider's ``parse_response``.

    Expects a provider config whose ``streaming`` is False (i.e. built
    from a config with ``streamingEnabled: false``) so Google-style
    non-streaming URLs are used.
    """
    merged_overrides = dict(overrides or {})
    if max_tokens is not None:
        merged_overrides["max_tokens"] = max_tokens
    body = provider_config["build_body"](messages, merged_overrides)
    body["stream"] = False
    body_bytes = json.dumps(body, ensure_ascii=True).encode("utf-8")

    if cancel_signal is not None and cancel_signal.is_set():
        raise RequestCancelled()

    close_client = client is None
    http = client if client is not None else _default_client()
    try:
        try:
            response = await http.post(
                provider_config["url"],
                headers=provider_config["headers"],
                content=body_bytes,
            )
        except httpx.HTTPError as exc:
            if cancel_signal is not None and cancel_signal.is_set():
                raise RequestCancelled() from exc
            raise LlmError(
                f"Network error reaching {provider_config['url']}. "
                "Check endpoint URL, API key, and connectivity."
            ) from exc
        try:
            await _check_http_response(response, config)
            raw = await response.aread()
            try:
                payload = json.loads(raw.decode("utf-8", "replace"))
            except Exception as exc:
                raise LlmError("Model returned an unparseable response") from exc
            content = provider_config["parse_response"](payload)
            if not content:
                raise LlmError("Model returned an empty non-streaming response")
            return content
        finally:
            await response.aclose()
    finally:
        if close_client:
            await http.aclose()
