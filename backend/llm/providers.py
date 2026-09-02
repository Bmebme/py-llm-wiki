"""LLM provider configuration and wire-format translation.

Port of llm_wiki's ``src/lib/llm-providers.ts`` (the v0 HTTP provider
subset) plus:

- ``normalize_endpoint`` from ``src/lib/endpoint-normalizer.ts``
- the Azure endpoint helpers from ``src/lib/azure-openai.ts``
- ``compute_context_budget`` / ``derive_anthropic_max_tokens`` from
  ``src/lib/context-budget.ts`` / ``llm-providers.ts``

Skipped (by design of this port):

- ``minimax`` provider: raises ``NotImplementedError``.
- ``claude-code`` / ``codex-cli``: subprocess transports live one layer
  up; calling ``get_provider_config`` for them raises ``FsError``.
- Full per-provider reasoning-capability nuance is ported to the extent
  the body builders need it (``normalize_reasoning_for_provider``,
  ``is_adaptive_anthropic_model``, ``is_gemini_thinking_level_model``
  from ``src/lib/reasoning-capabilities.ts``).

Each provider config is a dict:

    {
        "url": str,
        "headers": dict[str, str],
        "build_body": (messages, overrides) -> dict,
        "parse_stream": (line) -> str | None,
        "parse_response": (payload) -> str,
        "streaming": bool,
    }

``config`` (LlmConfig) is a plain dict mirroring the TS interface in
``src/stores/wiki-store.ts`` (v0 subset): provider, apiKey, model,
ollamaUrl, customEndpoint, azureApiVersion?, azureModelFamily?,
maxContextSize, apiMode?, reasoning?, streamingEnabled?, customHeaders?,
requestTimeoutMinutes?.

Messages are dicts ``{"role": "system"|"user"|"assistant", "content":
str | list[block]}`` where a block is ``{"type": "text", "text": str}``
or ``{"type": "image", "mediaType": str, "dataBase64": str}``.
"""

from __future__ import annotations

import json
import math
import re
import urllib.parse
from typing import Any, Callable, Optional

from backend.core.file_service import FsError

# --- Constants -------------------------------------------------------------

JSON_CONTENT_TYPE = "application/json"
AZURE_OPENAI_API_VERSION = "2024-10-21"

HTTP_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")

# endpoint-normalizer.ts
ALWAYS_WRONG_TAILS = re.compile(r"/+(chat/completions|embeddings)/?$", re.IGNORECASE)
MESSAGES_TAIL = re.compile(r"/+messages/?$", re.IGNORECASE)

# context-budget.ts
DEFAULT_MAX_CTX = 204_800
RESPONSE_RESERVE_FRAC = 0.15
INDEX_BUDGET_FRAC = 0.05
PAGE_BUDGET_FRAC = 0.5
PER_PAGE_FRAC = 0.3
PER_PAGE_FLOOR = 5_000


# --- Headers ---------------------------------------------------------------

def merge_llm_request_headers(
    custom: Optional[dict[str, str]], required: dict[str, str]
) -> dict[str, str]:
    """Port of mergeLlmRequestHeaders (llm-providers.ts:84-100).

    User-supplied gateway headers merge with protocol headers; names are
    compared case-insensitively so a differently-cased duplicate cannot
    bypass auth precedence. Invalid names and CR/LF values are ignored.
    """
    merged: dict[str, tuple[str, str]] = {}
    for name, value in (custom or {}).items():
        trimmed_name = name.strip()
        trimmed_value = value.strip()
        if not HTTP_HEADER_NAME_RE.fullmatch(trimmed_name) or not trimmed_value:
            continue
        if "\r" in trimmed_value or "\n" in trimmed_value:
            continue
        merged[trimmed_name.lower()] = (trimmed_name, trimmed_value)
    for name, value in required.items():
        if not value:
            continue
        merged[name.lower()] = (name, value)
    return {original: value for original, value in merged.values()}


def local_llm_origin_header() -> dict[str, str]:
    """Origin header for local-LLM endpoints (see llm-providers.ts:150)."""
    return {"Origin": "http://localhost"}


def is_local_or_private_http_endpoint(endpoint: str) -> bool:
    """Port of isLocalOrPrivateHttpEndpoint (llm-providers.ts:154-171)."""
    try:
        parsed = urllib.parse.urlparse(endpoint)
        host = (parsed.hostname or "").lower()
        # WHATWG URL.hostname keeps the brackets for IPv6 ("[::1]");
        # urllib strips them, so compare the bracketed forms too.
        if host == "localhost" or host.endswith(".localhost"):
            return True
        if host in ("127.0.0.1", "::1", "[::1]"):
            return True
        if host.startswith("10."):
            return True
        if host.startswith("192.168."):
            return True
        m = re.match(r"^172\.(\d+)\.", host)
        if m:
            second = int(m.group(1))
            if 16 <= second <= 31:
                return True
        return False
    except Exception:
        return False


# --- Azure OpenAI ----------------------------------------------------------

def is_azure_openai_endpoint(endpoint: str) -> bool:
    """Port of isAzureOpenAiEndpoint (azure-openai.ts:3-12)."""
    trimmed = endpoint.strip()
    if not trimmed:
        return False
    try:
        url = trimmed if re.match(r"^https?://", trimmed, re.IGNORECASE) else f"https://{trimmed}"
        parsed = urllib.parse.urlparse(url)
        return (parsed.hostname or "").lower().endswith(".openai.azure.com")
    except Exception:
        return re.search(
            r"(^|//)[^/?#]+\.openai\.azure\.com(?::\d+)?(?:[/?#]|$)",
            trimmed,
            re.IGNORECASE,
        ) is not None


def parse_azure_openai_endpoint(
    endpoint: str,
    fallback_deployment: str,
    fallback_api_version: str,
) -> Optional[dict[str, str]]:
    """Port of parseAzureOpenAiEndpoint (azure-openai.ts:25-62)."""
    trimmed = endpoint.strip()
    if not is_azure_openai_endpoint(trimmed):
        return None

    api_version = fallback_api_version.strip() or AZURE_OPENAI_API_VERSION
    q_match = re.search(r"[?&]api-version=([^&]+)", trimmed, re.IGNORECASE)
    if q_match:
        api_version = urllib.parse.unquote(q_match.group(1))

    without_query = trimmed.split("?")[0].rstrip("/")

    with_deployment = re.match(
        r"^(https?://[^/]+\.openai\.azure\.com)/openai/deployments/([^/]+)(?:/chat/completions)?$",
        without_query,
        re.IGNORECASE,
    )
    if with_deployment:
        return {
            "resourceBase": with_deployment.group(1),
            "deployment": urllib.parse.unquote(with_deployment.group(2)),
            "apiVersion": api_version,
        }

    resource_only = re.match(
        r"^(https?://[^/]+\.openai\.azure\.com)(?:/openai)?$",
        without_query,
        re.IGNORECASE,
    )
    if resource_only:
        deployment = fallback_deployment.strip()
        if not deployment:
            return None
        return {
            "resourceBase": resource_only.group(1),
            "deployment": deployment,
            "apiVersion": api_version,
        }

    return None


def _encode_uri_component(value: str) -> str:
    """encodeURIComponent: everything escaped except unreserved chars."""
    return urllib.parse.quote(value, safe="!~*'()")


def build_azure_openai_url(endpoint: str, deployment: str, api_version: str) -> str:
    """Port of buildAzureOpenAiUrl (azure-openai.ts:64-79)."""
    parsed = parse_azure_openai_endpoint(endpoint, deployment, api_version)
    if not parsed:
        trimmed = endpoint.rstrip("/")
        version = _encode_uri_component(api_version.strip() or AZURE_OPENAI_API_VERSION)
        deployment_path = f"/openai/deployments/{_encode_uri_component(deployment)}/chat/completions"
        return f"{trimmed}{deployment_path}?api-version={version}"
    version = _encode_uri_component(parsed["apiVersion"])
    dep = _encode_uri_component(parsed["deployment"])
    return f"{parsed['resourceBase']}/openai/deployments/{dep}/chat/completions?api-version={version}"


# --- URL parsing helpers ---------------------------------------------------

def _is_octet(o: str) -> bool:
    try:
        n = int(o)
    except ValueError:
        return False
    return 0 <= n <= 255


def _parse_url(url: str) -> urllib.parse.ParseResult:
    """WHATWG-style URL parse that raises on the shapes ``new URL()`` rejects.

    Python's urlparse is far more permissive than the WHATWG parser; the
    endpoint normalizer relies on ``new URL()`` throwing for five-octet
    IP-shaped hosts, octets > 255, and non-numeric ports. Reproduce those
    failure modes here.
    """
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise ValueError("missing protocol")
    parsed = urllib.parse.urlparse(url)
    if not parsed.hostname:
        raise ValueError("missing host")
    host = parsed.hostname
    if re.fullmatch(r"\d+(?:\.\d+)+", host):
        octets = host.split(".")
        if len(octets) != 4 or not all(_is_octet(o) for o in octets):
            raise ValueError("invalid IPv4 host")
    if parsed.netloc.endswith(":"):
        raise ValueError("empty port")
    if ":" in parsed.netloc.rsplit("@", 1)[-1]:
        port_part = parsed.netloc.rsplit(":", 1)[1]
        if not port_part.isdigit() or int(port_part) > 65535:
            raise ValueError("invalid port")
    return parsed


def _origin(parsed: urllib.parse.ParseResult) -> str:
    """WHATWG URL.origin (default ports omitted)."""
    host = parsed.hostname or ""
    port = parsed.port
    if port is None:
        return f"{parsed.scheme}://{host}"
    if (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443):
        return f"{parsed.scheme}://{host}"
    return f"{parsed.scheme}://{host}:{port}"


# --- Endpoint normalization (endpoint-normalizer.ts) -----------------------

def normalize_endpoint(raw: Optional[str], mode: str) -> dict[str, Any]:
    """Port of normalizeEndpoint (endpoint-normalizer.ts:42-168).

    Returns ``{"normalized", "changed", "warning"}`` where ``warning`` is
    None when the input is fine.
    """
    trimmed = (raw or "").strip()
    if not trimmed:
        return {"normalized": "", "changed": False, "warning": None}

    # Detect missing protocol — never auto-add https:// (masks typos).
    missing_protocol = re.match(r"^https?://", trimmed, re.IGNORECASE) is None
    if missing_protocol:
        normalized = re.sub(r"/+$", "", trimmed)
        return {
            "normalized": normalized,
            "changed": normalized != trimmed,
            "warning": "URL should start with http:// or https://",
        }

    url = trimmed
    notes: list[str] = []

    try:
        parsed = _parse_url(trimmed)
    except ValueError:
        normalized = re.sub(r"/+$", "", trimmed)
        return {
            "normalized": normalized,
            "changed": normalized != trimmed,
            "warning": "URL is not well-formed — check for typos in the host / port / path.",
        }

    # IP-shaped hostnames that aren't valid IPv4 get flagged (unreachable
    # in practice: _parse_url already rejects them, mirroring new URL()).
    host = parsed.hostname or ""
    looks_numeric_dotted = re.fullmatch(r"\d+(?:\.\d+)+", host) is not None
    if looks_numeric_dotted:
        octets = host.split(".")
        valid_ipv4 = len(octets) == 4 and all(_is_octet(o) for o in octets)
        if not valid_ipv4:
            notes.append(
                f'Host "{host}" looks like an IPv4 address but has {len(octets)} octets '
                "(valid IPv4 has exactly 4, each 0-255)."
            )

    # Strip trailing slashes (cheap, always safe)
    url = re.sub(r"/+$", "", url)

    # Azure OpenAI: keep /openai/deployments/{name} on the stored base.
    if mode == "azure" or is_azure_openai_endpoint(url):
        try:
            u = _parse_url(url if "://" in url else f"https://{url}")
            pathname = re.sub(r"/+$", "", u.path)
            if re.search(r"/chat/completions/?$", pathname, re.IGNORECASE):
                pathname = re.sub(
                    r"/chat/completions/?$", "", pathname, flags=re.IGNORECASE
                )
                notes.append(
                    'stripped trailing "chat/completions" — Azure appends this per request'
                )
            url = _origin(u) + pathname
            if u.query:
                notes.append("stripped query string — api-version is configured separately")
        except ValueError:
            if re.search(r"/chat/completions/?(?:$|\?)", url, re.IGNORECASE):
                url = re.sub(
                    r"/chat/completions/?(?=$|\?)", "", url, flags=re.IGNORECASE
                )
                notes.append(
                    'stripped trailing "chat/completions" — Azure appends this per request'
                )
        changed = url != trimmed
        return {
            "normalized": url,
            "changed": changed,
            "warning": " ".join(notes) if notes else None,
        }

    # Strip request-path tails users paste by accident.
    wrong_tail = ALWAYS_WRONG_TAILS.search(url)
    if wrong_tail:
        url = ALWAYS_WRONG_TAILS.sub("", url)
        tail = wrong_tail.group(0).lstrip("/").rstrip("/")
        notes.append(
            f'stripped trailing "{tail}" — this is appended per-request, not part of the base URL'
        )
    elif mode == "chat_completions":
        messages_tail = MESSAGES_TAIL.search(url)
        if messages_tail:
            url = MESSAGES_TAIL.sub("", url)
            tail = messages_tail.group(0).lstrip("/").rstrip("/")
            notes.append(
                f'stripped trailing "{tail}" — this is an Anthropic-wire path, not a chat/completions base'
            )

    # After stripping, check for the "bare host, no version segment" case
    # (chat_completions mode only).
    if mode == "chat_completions":
        try:
            u = _parse_url(url)
            pathname = re.sub(r"/+$", "", u.path)
            has_version_segment = re.search(
                r"/(?:v\d+|paas/v\d+|openai/v\d+|api/v\d+)$",
                pathname,
                re.IGNORECASE,
            )
            if not has_version_segment and not notes:
                notes.append(
                    'URL has no version segment (expected e.g. "/v1"). '
                    "Double-check the provider's docs."
                )
        except ValueError:
            # Malformed URL — leave alone, the HTTP client will fail loudly.
            pass

    changed = url != trimmed
    return {
        "normalized": url,
        "changed": changed,
        "warning": " ".join(notes) if notes else None,
    }


# --- Anthropic URL / headers ----------------------------------------------

def _is_minimax_anthropic_host(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.hostname in ("api.minimax.io", "api.minimaxi.com")
    except Exception:
        return False


def _normalize_minimax_anthropic_base(base: str) -> str:
    if not _is_minimax_anthropic_host(base):
        return base
    try:
        parsed = urllib.parse.urlparse(base)
        path = re.sub(r"/+$", "", parsed.path)
        if path in ("", "/") or re.fullmatch(r"/v\d+(?:/messages)?", path, re.IGNORECASE):
            return f"{_origin(parsed)}/anthropic"
    except Exception:
        return base
    return base


def build_anthropic_url(base: str) -> str:
    """Port of buildAnthropicUrl (llm-providers.ts:774-779).

    .../v1/messages -> as-is; .../v1 -> append /messages;
    .../anthropic -> append /v1/messages; bare host -> /v1/messages.
    """
    trimmed = _normalize_minimax_anthropic_base(base).rstrip("/")
    if re.search(r"/v\d+/messages$", trimmed, re.IGNORECASE):
        return trimmed
    if re.search(r"/v\d+$", trimmed, re.IGNORECASE):
        return f"{trimmed}/messages"
    return f"{trimmed}/v1/messages"


def _requires_bearer_auth(url: str) -> bool:
    """Port of requiresBearerAuth (llm-providers.ts:669-692)."""
    normalized = url.lower().rstrip("/")
    return (
        normalized.startswith("https://api.minimax.io/anthropic")
        or normalized.startswith("https://api.minimaxi.com/anthropic")
        or normalized.startswith("https://coding.dashscope.aliyuncs.com/apps/anthropic")
        or re.search(
            r"(?:^https://|^)token-plan-cn\.xiaomimimo\.com/anthropic(?:/|$)",
            normalized,
            re.IGNORECASE,
        )
        is not None
        or normalized.startswith("https://api.kimi.com/coding")
        or normalized.startswith("https://api.moonshot.ai/anthropic")
        or normalized.startswith("https://api.moonshot.cn/anthropic")
    )


def build_anthropic_headers(config: dict[str, Any], url: str) -> dict[str, str]:
    """Port of buildAnthropicHeaders (llm-providers.ts:781-794)."""
    api_key = config.get("apiKey") or ""
    base: dict[str, str] = {
        "Content-Type": JSON_CONTENT_TYPE,
        "anthropic-version": "2023-06-01",
    }
    if _requires_bearer_auth(url):
        base["Authorization"] = f"Bearer {api_key}"
    else:
        base["x-api-key"] = api_key
        base["anthropic-dangerous-direct-browser-access"] = "true"
    return merge_llm_request_headers(config.get("customHeaders"), base)


# --- Reasoning normalization (reasoning-capabilities.ts) -------------------

AUTO_ONLY = ("auto",)
OPENAI_LEVELS = ("auto", "low", "medium", "high")
BUDGET_LEVELS = ("auto", "off", "low", "medium", "high", "max", "custom")
THINKING_REQUIRED_BUDGETS = ("auto", "low", "medium", "high", "max", "custom")
THINKING_REQUIRED_LEVELS = ("auto", "low", "medium", "high", "max")
OLLAMA_LEVELS = ("auto", "off", "low", "medium", "high", "max")
TOGGLE_LEVELS = ("auto", "off")
DEEPSEEK_LEVELS = ("auto", "off", "high", "max")


def _capabilities(modes: tuple[str, ...], custom_range: tuple[int, int] = (1, 32_768)) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def normalize(requested: dict[str, Any]) -> dict[str, Any]:
        if requested.get("mode") not in modes:
            return {"mode": "auto"}
        if requested.get("mode") != "custom":
            return {"mode": requested["mode"]}
        budget = min(custom_range[1], math.floor(requested.get("budgetTokens") or 0))
        if budget > 0 and budget < custom_range[0]:
            return {"mode": "custom", "budgetTokens": custom_range[0]}
        if budget > 0:
            return {"mode": "custom", "budgetTokens": budget}
        return {"mode": "auto"}

    return normalize


def _is_claude46_or_later(model: str) -> bool:
    return (
        re.search(r"claude-(?:opus|sonnet|haiku)-4[-_.]?(?:[6-9]|\d{2,})(?:[-_.]|$)", model, re.IGNORECASE)
        is not None
    )


def _is_gemini25_pro(model: str) -> bool:
    return re.search(r"gemini[-_.]?2\.5[-_.]?pro(?:[-_.]|$)", model, re.IGNORECASE) is not None


def _is_gemini3(model: str) -> bool:
    return re.search(r"gemini[-_.]?3(?:[-_.]|$)", model, re.IGNORECASE) is not None


def _is_openai_reasoning_model(config: dict[str, Any]) -> bool:
    if config.get("provider") == "azure" and config.get("azureModelFamily") == "gpt5":
        return True
    model = (config.get("model") or "").strip().lower()
    return re.search(r"^(?:gpt-5|o\d+)(?:[.\-_]|$)", model) is not None


def _resolve_reasoning_capabilities(config: dict[str, Any]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    provider = config.get("provider")
    model = config.get("model") or ""
    if provider in ("claude-code", "codex-cli"):
        return _capabilities(AUTO_ONLY)
    if provider == "ollama":
        return _capabilities(OLLAMA_LEVELS)
    if provider == "google":
        if _is_gemini3(model):
            return _capabilities(THINKING_REQUIRED_LEVELS)
        if _is_gemini25_pro(model):
            return _capabilities(THINKING_REQUIRED_BUDGETS, (128, 32_768))
        return _capabilities(BUDGET_LEVELS)
    if provider == "anthropic":
        return _capabilities(
            THINKING_REQUIRED_LEVELS if _is_claude46_or_later(model) else BUDGET_LEVELS,
            (1024, 32_768),
        )
    if provider == "minimax":
        return _capabilities(AUTO_ONLY)
    if provider in ("openai", "azure"):
        return _capabilities(OPENAI_LEVELS if _is_openai_reasoning_model(config) else AUTO_ONLY)
    if provider == "custom":
        endpoint = (config.get("customEndpoint") or "").lower()
        if re.search(r"api\.deepseek\.(?:com|cn)(?:[:/]|$)", endpoint):
            return _capabilities(DEEPSEEK_LEVELS)
        if re.search(r"xiaomimimo\.com(?:[:/]|$)", endpoint):
            return _capabilities(TOGGLE_LEVELS)
        return _capabilities(AUTO_ONLY)
    return _capabilities(AUTO_ONLY)


def normalize_reasoning_for_provider(config: dict[str, Any], requested: dict[str, Any]) -> dict[str, Any]:
    """Port of normalizeReasoningForProvider (reasoning-capabilities.ts:102-107)."""
    return _resolve_reasoning_capabilities(config)(requested)


def is_adaptive_anthropic_model(config: dict[str, Any]) -> bool:
    return config.get("provider") == "anthropic" and _is_claude46_or_later(config.get("model") or "")


def is_gemini_thinking_level_model(config: dict[str, Any]) -> bool:
    return config.get("provider") == "google" and _is_gemini3(config.get("model") or "")


# --- Context budget --------------------------------------------------------

def compute_context_budget(max_context_size: Optional[float]) -> dict[str, float]:
    """Port of computeContextBudget (context-budget.ts:68-100)."""
    max_ctx = (
        max_context_size
        if isinstance(max_context_size, (int, float)) and max_context_size > 0
        else DEFAULT_MAX_CTX
    )
    response_reserve = math.floor(max_ctx * RESPONSE_RESERVE_FRAC)
    index_budget = math.floor(max_ctx * INDEX_BUDGET_FRAC)
    page_budget = math.floor(max_ctx * PAGE_BUDGET_FRAC)
    max_page_size = min(
        page_budget,
        max(PER_PAGE_FLOOR, math.floor(page_budget * PER_PAGE_FRAC)),
    )
    return {
        "maxCtx": max_ctx,
        "responseReserve": response_reserve,
        "indexBudget": index_budget,
        "pageBudget": page_budget,
        "maxPageSize": max_page_size,
    }


def derive_anthropic_max_tokens(max_context_size: Optional[float]) -> int:
    """Port of deriveAnthropicMaxTokens (llm-providers.ts:897-900).

    clamp(1, 16384, floor(responseReserve / 3)).
    """
    response_reserve = compute_context_budget(max_context_size)["responseReserve"]
    return max(1, min(16_384, math.floor(response_reserve / 3)))


# --- Endpoint / model classification helpers -------------------------------

def is_deepseek_endpoint(config: dict[str, Any]) -> bool:
    return re.search(r"api\.deepseek\.(?:com|cn)(?:[:/]|$)", config.get("customEndpoint") or "", re.IGNORECASE) is not None


def supports_deepseek_thinking_param(config: dict[str, Any]) -> bool:
    return re.search(r"deepseek[-_]?v4", config.get("model") or "", re.IGNORECASE) is not None


def is_kimi_endpoint(config: dict[str, Any]) -> bool:
    endpoint = config.get("customEndpoint") or ""
    return (
        re.search(r"api\.moonshot\.(ai|cn)(?:[:/]|$)", endpoint, re.IGNORECASE) is not None
        or re.search(r"api\.kimi\.com/coding(?:/|$)", endpoint, re.IGNORECASE) is not None
    )


def is_xiaomi_mimo_endpoint(config: dict[str, Any]) -> bool:
    return re.search(r"\.?xiaomimimo\.com(?::|/|$)", config.get("customEndpoint") or "", re.IGNORECASE) is not None


def is_big_model_endpoint(config: dict[str, Any]) -> bool:
    return re.search(r"(?:^|//)open\.bigmodel\.cn(?:[:/]|$)", config.get("customEndpoint") or "", re.IGNORECASE) is not None


def is_glm_vision_model(model: str) -> bool:
    normalized = model.strip().lower()
    return (
        re.search(r"(?:^|[-_.])glm[-_.]5v[-_.]turbo(?:[-_.]|$)", normalized) is not None
        or re.search(r"(?:^|[-_.])glm[-_.]4\.?6v(?:[-_.]|$)", normalized) is not None
        or re.search(r"(?:^|[-_.])glm[-_.]4\.?5v(?:[-_.]|$)", normalized) is not None
        or re.search(r"(?:^|[-_.])glm[-_.]4v(?:[-_.]|$)", normalized) is not None
    )


def _is_openai_strict_completion_model(config: dict[str, Any]) -> bool:
    if (
        config.get("provider") == "azure"
        or (config.get("provider") == "custom" and is_azure_openai_endpoint(config.get("customEndpoint") or ""))
    ) and config.get("azureModelFamily") == "gpt5":
        return True
    model = (config.get("model") or "").strip().lower()
    strict_model = re.match(r"^gpt-5(?:[.\-_]|$)", model) is not None or re.match(r"^o\d+(?:[.\-_]|$)", model) is not None
    if not strict_model:
        return False
    if config.get("provider") in ("openai", "azure"):
        return True
    return config.get("provider") == "custom" and is_azure_openai_endpoint(config.get("customEndpoint") or "")


def _is_minimax_m3_model(model: str) -> bool:
    return re.match(r"^minimax-m3(?:[-_.]|$)", model.strip(), re.IGNORECASE) is not None


def _is_official_minimax_anthropic_url(url: str) -> bool:
    if not _is_minimax_anthropic_host(url):
        return False
    try:
        parsed = urllib.parse.urlparse(url)
        return re.match(r"/anthropic(?:/|$)", parsed.path, re.IGNORECASE) is not None
    except Exception:
        return False


def has_image_content(messages: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(m.get("content"), list)
        and any(isinstance(b, dict) and b.get("type") == "image" for b in m["content"])
        for m in messages
    )


def assert_minimax_image_support(url: str, model: str, messages: list[dict[str, Any]]) -> None:
    if not _is_official_minimax_anthropic_url(url) or not has_image_content(messages) or _is_minimax_m3_model(model):
        return
    raise FsError(
        "MiniMax image input is supported only by MiniMax-M3 on the official Anthropic-compatible "
        "endpoint. Switch the model to MiniMax-M3 or use another vision-capable provider."
    )


def assert_big_model_image_support(config: dict[str, Any], messages: list[dict[str, Any]]) -> None:
    if not is_big_model_endpoint(config) or not has_image_content(messages) or is_glm_vision_model(config.get("model") or ""):
        return
    raise FsError(
        "Zhipu BigModel image input is supported only by GLM vision models. "
        "Switch to glm-5v-turbo, glm-4.6v, glm-4.5v, or glm-4v-plus."
    )


def supports_image_input(config: dict[str, Any]) -> bool:
    """Port of supportsImageInput (llm-providers.ts:750-759)."""
    if config.get("provider") == "codex-cli":
        return False
    if config.get("provider") == "minimax":
        return _is_minimax_m3_model(config.get("model") or "")
    if is_big_model_endpoint(config):
        return is_glm_vision_model(config.get("model") or "")
    if config.get("provider") == "custom" and (config.get("apiMode") or "chat_completions") == "anthropic_messages":
        url = build_anthropic_url(config.get("customEndpoint") or "")
        return not _is_official_minimax_anthropic_url(url) or _is_minimax_m3_model(config.get("model") or "")
    return True


# --- Wire parsing ----------------------------------------------------------

def parse_openai_line(line: str) -> Optional[str]:
    """Port of parseOpenAiLine (llm-providers.ts:173-185)."""
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if data == "[DONE]":
        return None
    try:
        parsed = json.loads(data)  # noqa: F821 - imported below
        choices = parsed.get("choices")
        if choices:
            delta = choices[0].get("delta")
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str):
                    return content
        return None
    except Exception:
        return None


def _text_from_unknown_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    out: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str):
            out.append(text)
    return "".join(out)


def parse_openai_response(payload: Any) -> str:
    """Port of parseOpenAiResponse (llm-providers.ts:199-202)."""
    root = payload if isinstance(payload, dict) else {}
    choices = root.get("choices")
    if not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if message is None:
        return ""
    return _text_from_unknown_content(message.get("content"))


def parse_anthropic_response(payload: Any) -> str:
    """Port of parseAnthropicResponse (llm-providers.ts:204-207)."""
    root = payload if isinstance(payload, dict) else {}
    return _text_from_unknown_content(root.get("content"))


def parse_google_response(payload: Any) -> str:
    """Port of parseGoogleResponse (llm-providers.ts:209-217)."""
    root = payload if isinstance(payload, dict) else {}
    candidates = root.get("candidates") or []
    parts = []
    if candidates and isinstance(candidates[0], dict):
        content = candidates[0].get("content")
        if isinstance(content, dict):
            parts = content.get("parts") or []
    out = ""
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("thought"):
            continue
        text = part.get("text")
        if text is not None:
            out += str(text)
    return out


def parse_anthropic_line(line: str) -> Optional[str]:
    """Port of parseAnthropicLine (llm-providers.ts:219-260)."""
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if data == "[DONE]":
        return None
    try:
        parsed = json.loads(data)
        delta = parsed.get("delta")
        if (
            parsed.get("type") == "content_block_delta"
            and isinstance(delta, dict)
            and (delta.get("type") == "text_delta" or isinstance(delta.get("text"), str))
        ):
            return delta.get("text")
        # Some Anthropic-compatible gateways emit the complete message in
        # a single SSE event instead of incremental deltas.
        if parsed.get("type") == "message" and isinstance(parsed.get("content"), list):
            text = "".join(
                b.get("text", "") if isinstance(b.get("text"), str) else ""
                for b in parsed["content"]
            )
            return text if text else None
        # Fallback: misconfigured proxies occasionally return OpenAI-shaped
        # chunks on an Anthropic wire.
        choices = parsed.get("choices")
        if choices:
            choice_delta = choices[0].get("delta")
            if isinstance(choice_delta, dict) and isinstance(choice_delta.get("content"), str):
                return choice_delta["content"]
        return None
    except Exception:
        return None


def parse_google_line(line: str) -> Optional[str]:
    """Port of parseGoogleLine (llm-providers.ts:262-289)."""
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    try:
        parsed = json.loads(data)
        candidates = parsed.get("candidates")
        if not candidates:
            return None
        content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        if not parts:
            return None
        out = ""
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("thought"):
                continue
            text = part.get("text")
            if isinstance(text, str) and text:
                out += text
        return out if out else None
    except Exception:
        return None


# --- Content translation ---------------------------------------------------

def _to_openai_content(content: Any) -> Any:
    """Port of toOpenAiContent (llm-providers.ts:307-321)."""
    if isinstance(content, str):
        return content
    if all(isinstance(b, dict) and b.get("type") == "text" for b in content):
        return "".join(b.get("text", "") for b in content)
    out = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "text":
            out.append({"type": "text", "text": b.get("text", "")})
        elif isinstance(b, dict):
            out.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{b.get('mediaType', '')};base64,{b.get('dataBase64', '')}"},
                }
            )
    return out


def _to_anthropic_content(content: Any) -> Any:
    """Port of toAnthropicContent (llm-providers.ts:532-548)."""
    if isinstance(content, str):
        return content
    if all(isinstance(b, dict) and b.get("type") == "text" for b in content):
        return "".join(b.get("text", "") for b in content)
    out = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "text":
            out.append({"type": "text", "text": b.get("text", "")})
        elif isinstance(b, dict):
            out.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": b.get("mediaType", ""),
                        "data": b.get("dataBase64", ""),
                    },
                }
            )
    return out


def _flatten_anthropic_system(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def _build_anthropic_system(system_text: str) -> Optional[list[dict[str, Any]]]:
    if not system_text:
        return None
    return [
        {
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _to_google_parts(content: Any) -> list[dict[str, Any]]:
    """Port of toGoogleParts (llm-providers.ts:803-814)."""
    if isinstance(content, str):
        return [{"text": content}]
    out = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "text":
            out.append({"text": b.get("text", "")})
        elif isinstance(b, dict):
            out.append(
                {
                    "inline_data": {
                        "mime_type": b.get("mediaType", ""),
                        "data": b.get("dataBase64", ""),
                    }
                }
            )
    return out


def _flatten_google_system_parts(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


# --- Body builders ---------------------------------------------------------

def _strip_wire_agnostic_overrides(overrides: Optional[dict[str, Any]]) -> dict[str, Any]:
    return {k: v for k, v in (overrides or {}).items() if k != "reasoning"}


def _effective_reasoning(config: dict[str, Any], overrides: Optional[dict[str, Any]]) -> dict[str, Any]:
    requested = (
        (overrides or {}).get("reasoning")
        or config.get("reasoning")
        or {"mode": "auto"}
    )
    return normalize_reasoning_for_provider(config, requested)


def build_openai_body(
    messages: list[dict[str, Any]],
    overrides: Optional[dict[str, Any]] = None,
    streaming: bool = True,
) -> dict[str, Any]:
    """Port of buildOpenAiBody (llm-providers.ts:323-336)."""
    translated = [
        {"role": m.get("role"), "content": _to_openai_content(m.get("content"))}
        for m in messages
    ]
    body: dict[str, Any] = {"messages": translated, "stream": streaming}
    body.update(_strip_wire_agnostic_overrides(overrides))
    return body


def _adapt_openai_strict_completion_body(config: dict[str, Any], body: dict[str, Any]) -> None:
    if not _is_openai_strict_completion_model(config):
        return
    if isinstance(body.get("max_tokens"), (int, float)):
        body["max_completion_tokens"] = body["max_tokens"]
        body.pop("max_tokens", None)
    body.pop("temperature", None)
    body.pop("top_p", None)
    body.pop("top_k", None)


def _adapt_kimi_body(config: dict[str, Any], body: dict[str, Any]) -> None:
    if not is_kimi_endpoint(config):
        return
    body.pop("temperature", None)


def _adapt_xiaomi_mimo_body(
    config: dict[str, Any],
    body: dict[str, Any],
    reasoning: dict[str, Any],
) -> None:
    if not is_xiaomi_mimo_endpoint(config):
        return
    if isinstance(body.get("max_tokens"), (int, float)):
        body["max_completion_tokens"] = body["max_tokens"]
        body.pop("max_tokens", None)
    if reasoning.get("mode") == "off":
        body["thinking"] = {"type": "disabled"}
    else:
        body.pop("temperature", None)


def build_openai_compatible_body(
    config: dict[str, Any],
    messages: list[dict[str, Any]],
    overrides: Optional[dict[str, Any]] = None,
    streaming: bool = True,
) -> dict[str, Any]:
    """Port of buildOpenAiCompatibleBody (llm-providers.ts:449-519)."""
    assert_big_model_image_support(config, messages)
    reasoning = _effective_reasoning(config, overrides)
    body = build_openai_body(messages, _strip_wire_agnostic_overrides(overrides), streaming)
    _adapt_openai_strict_completion_body(config, body)
    _adapt_kimi_body(config, body)
    _adapt_xiaomi_mimo_body(config, body, reasoning)

    if is_deepseek_endpoint(config):
        if supports_deepseek_thinking_param(config):
            if reasoning.get("mode") == "off":
                body["thinking"] = {"type": "disabled"}
            elif reasoning.get("mode") != "auto":
                body["thinking"] = {"type": "enabled"}
                if reasoning.get("mode") in ("high", "max"):
                    body["reasoning_effort"] = reasoning["mode"]
        return body

    if config.get("provider") == "ollama":
        mode = reasoning.get("mode")
        if mode == "off":
            body["reasoning_effort"] = "none"
        elif mode in ("low", "medium", "high"):
            body["reasoning_effort"] = mode
        elif mode == "max":
            body["reasoning_effort"] = "high"
        return body

    if config.get("provider") == "openai" and reasoning.get("mode") not in ("auto", "off"):
        mode = reasoning.get("mode")
        if mode in ("low", "medium", "high"):
            body["reasoning_effort"] = mode

    return body


def build_anthropic_body(
    messages: list[dict[str, Any]],
    overrides: Optional[dict[str, Any]] = None,
    streaming: bool = True,
) -> dict[str, Any]:
    """Port of buildAnthropicBody (llm-providers.ts:578-607)."""
    system_messages = [m for m in messages if m.get("role") == "system"]
    conversation_messages = [
        {"role": m.get("role"), "content": _to_anthropic_content(m.get("content"))}
        for m in messages
        if m.get("role") != "system"
    ]
    system_text = "\n".join(_flatten_anthropic_system(m.get("content")) for m in system_messages)
    system = _build_anthropic_system(system_text)

    body: dict[str, Any] = {
        "messages": conversation_messages,
        "stream": streaming,
        "max_tokens": (overrides or {}).get("max_tokens", 4096),
    }
    if system is not None:
        body["system"] = system
    if (overrides or {}).get("temperature") is not None:
        body["temperature"] = overrides["temperature"]
    if (overrides or {}).get("top_p") is not None:
        body["top_p"] = overrides["top_p"]
    if (overrides or {}).get("top_k") is not None:
        body["top_k"] = overrides["top_k"]
    if (overrides or {}).get("stop") is not None:
        stop = overrides["stop"]
        body["stop_sequences"] = stop if isinstance(stop, list) else [stop]
    return body


def build_anthropic_body_with_reasoning(
    config: dict[str, Any],
    messages: list[dict[str, Any]],
    overrides: Optional[dict[str, Any]] = None,
    streaming: bool = True,
) -> dict[str, Any]:
    """Port of buildAnthropicBodyWithReasoning (llm-providers.ts:609-650)."""
    body = build_anthropic_body(messages, overrides, streaming)
    reasoning = _effective_reasoning(config, overrides)
    if reasoning.get("mode") in ("auto", "off"):
        return body

    if is_adaptive_anthropic_model(config):
        body["thinking"] = {"type": "adaptive"}
        mode = reasoning.get("mode")
        effort = "high" if mode == "custom" else ("max" if mode == "max" else mode)
        body["output_config"] = {"effort": effort}
        for key in ("temperature", "top_p", "top_k"):
            body.pop(key, None)
        return body

    mode = reasoning.get("mode")
    if mode == "custom" and reasoning.get("budgetTokens") is not None:
        budget = reasoning["budgetTokens"]
    elif mode == "low":
        budget = 1024
    elif mode == "medium":
        budget = 4096
    else:
        budget = 8192
    budget_tokens = max(1024, budget)
    if body["max_tokens"] <= budget_tokens:
        body["max_tokens"] = budget_tokens + 1
    body["thinking"] = {"type": "enabled", "budget_tokens": budget_tokens}
    for key in ("temperature", "top_p", "top_k"):
        body.pop(key, None)
    return body


def _anthropic_build_body(
    url: str,
    model: str,
    config: dict[str, Any],
    messages: list[dict[str, Any]],
    overrides: Optional[dict[str, Any]],
    streaming: bool,
    anthropic_budget_tokens: int,
) -> dict[str, Any]:
    """Shared build_body for the anthropic and custom/anthropic_messages
    branches (llm-providers.ts:936-941 / 1063-1068): image support guard
    first, then the Anthropic body with model appended."""
    assert_minimax_image_support(url, model, messages)
    body = build_anthropic_body_with_reasoning(
        config,
        messages,
        {"max_tokens": anthropic_budget_tokens, **(overrides or {})},
        streaming,
    )
    body["model"] = model
    return body


def build_google_body(
    config: dict[str, Any],
    messages: list[dict[str, Any]],
    overrides: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Port of buildGoogleBody (llm-providers.ts:821-889)."""
    system_messages = [m for m in messages if m.get("role") == "system"]
    conversation_messages = [m for m in messages if m.get("role") != "system"]

    contents = [
        {
            "role": "model" if m.get("role") == "assistant" else "user",
            "parts": _to_google_parts(m.get("content")),
        }
        for m in conversation_messages
    ]

    system_instruction = (
        {
            "parts": [
                {"text": _flatten_google_system_parts(m.get("content"))}
                for m in system_messages
            ]
        }
        if system_messages
        else None
    )

    generation_config: dict[str, Any] = {}
    o = overrides or {}
    if o.get("temperature") is not None:
        generation_config["temperature"] = o["temperature"]
    if o.get("top_p") is not None:
        generation_config["topP"] = o["top_p"]
    if o.get("top_k") is not None:
        generation_config["topK"] = o["top_k"]
    if o.get("max_tokens") is not None:
        generation_config["maxOutputTokens"] = o["max_tokens"]
    if o.get("stop") is not None:
        stop = o["stop"]
        generation_config["stopSequences"] = stop if isinstance(stop, list) else [stop]

    reasoning = _effective_reasoning(config, overrides)
    mode = reasoning.get("mode")
    if mode == "off":
        generation_config["thinkingConfig"] = {"thinkingBudget": 0}
    elif mode != "auto":
        if is_gemini_thinking_level_model(config):
            level = mode if mode in ("low", "medium") else "high"
            generation_config["thinkingConfig"] = {"thinkingLevel": level}
        else:
            if mode == "custom" and reasoning.get("budgetTokens") is not None:
                budget = reasoning["budgetTokens"]
            elif mode == "low":
                budget = 1024
            elif mode == "medium":
                budget = 4096
            else:
                budget = 8192
            generation_config["thinkingConfig"] = {"thinkingBudget": budget}

    body: dict[str, Any] = {"contents": contents}
    if system_instruction is not None:
        body["systemInstruction"] = system_instruction
    if generation_config:
        body["generationConfig"] = generation_config
    return body


# --- Provider config dispatch ----------------------------------------------

def get_provider_config(config: dict[str, Any]) -> dict[str, Any]:
    """Port of getProviderConfig (llm-providers.ts:902-1121), v0 subset."""
    provider = config["provider"]
    api_key = config.get("apiKey") or ""
    model = config.get("model") or ""
    ollama_url = config.get("ollamaUrl") or ""
    custom_endpoint = config.get("customEndpoint") or ""
    streaming = config.get("streamingEnabled", True) is not False

    anthropic_budget_tokens = derive_anthropic_max_tokens(config.get("maxContextSize"))

    if provider == "openai":
        return {
            "url": "https://api.openai.com/v1/chat/completions",
            "headers": merge_llm_request_headers(
                config.get("customHeaders"),
                {
                    "Content-Type": JSON_CONTENT_TYPE,
                    "Authorization": f"Bearer {api_key}",
                },
            ),
            "build_body": lambda messages, overrides=None: {
                **build_openai_compatible_body(config, messages, overrides, streaming),
                "model": model,
            },
            "parse_stream": parse_openai_line,
            "parse_response": parse_openai_response,
            "streaming": streaming,
        }

    if provider == "anthropic":
        url = build_anthropic_url("https://api.anthropic.com")
        return {
            "url": url,
            "headers": build_anthropic_headers(config, url),
            "build_body": lambda messages, overrides=None: _anthropic_build_body(
                url, model, config, messages, overrides, streaming, anthropic_budget_tokens
            ),
            "parse_stream": parse_anthropic_line,
            "parse_response": parse_anthropic_response,
            "streaming": streaming,
        }

    if provider == "google":
        encoded_model = _encode_uri_component(model)
        return {
            "url": (
                f"https://generativelanguage.googleapis.com/v1beta/models/{encoded_model}"
                ":streamGenerateContent?alt=sse"
                if streaming
                else f"https://generativelanguage.googleapis.com/v1beta/models/{encoded_model}:generateContent"
            ),
            "headers": merge_llm_request_headers(
                config.get("customHeaders"),
                {
                    "Content-Type": JSON_CONTENT_TYPE,
                    "x-goog-api-key": api_key,
                },
            ),
            "build_body": lambda messages, overrides=None: build_google_body(
                config,
                messages,
                {**(overrides or {}), "reasoning": _effective_reasoning(config, overrides)},
            ),
            "parse_stream": parse_google_line,
            "parse_response": parse_google_response,
            "streaming": streaming,
        }

    if provider == "azure":
        url = build_azure_openai_url(
            custom_endpoint,
            model,
            config.get("azureApiVersion") or AZURE_OPENAI_API_VERSION,
        )
        return {
            "url": url,
            "headers": merge_llm_request_headers(
                config.get("customHeaders"),
                {
                    "Content-Type": JSON_CONTENT_TYPE,
                    "api-key": api_key,
                },
            ),
            "build_body": lambda messages, overrides=None: build_openai_compatible_body(
                config, messages, overrides, streaming
            ),
            "parse_stream": parse_openai_line,
            "parse_response": parse_openai_response,
            "streaming": streaming,
        }

    if provider == "ollama":
        # Strip a pasted full path or bare trailing /v1 so users can enter
        # either form.
        ollama_base = re.sub(r"/+$", "", ollama_url)
        if re.search(r"/v1/chat/completions$", ollama_base, re.IGNORECASE):
            ollama_base = re.sub(r"/v1/chat/completions$", "", ollama_base, flags=re.IGNORECASE)
        elif re.search(r"/v1$", ollama_base, re.IGNORECASE):
            ollama_base = re.sub(r"/v1$", "", ollama_base, flags=re.IGNORECASE)
        return {
            "url": f"{ollama_base}/v1/chat/completions",
            "headers": merge_llm_request_headers(
                config.get("customHeaders"),
                {
                    "Content-Type": JSON_CONTENT_TYPE,
                    **local_llm_origin_header(),
                },
            ),
            "build_body": lambda messages, overrides=None: {
                **build_openai_compatible_body(config, messages, overrides, streaming),
                "model": model,
            },
            "parse_stream": parse_openai_line,
            "parse_response": parse_openai_response,
            "streaming": streaming,
        }

    if provider == "minimax":
        raise NotImplementedError(
            "minimax provider is not implemented in this port; "
            "use provider 'custom' with apiMode 'anthropic_messages' "
            "and an https://api.minimax.io/anthropic endpoint instead"
        )

    if provider in ("claude-code", "codex-cli"):
        raise FsError(
            f"{provider} provider uses subprocess transport; getProviderConfig should not be called for it"
        )

    if provider == "custom":
        mode = config.get("apiMode") or "chat_completions"
        if mode == "anthropic_messages":
            url = build_anthropic_url(custom_endpoint)
            return {
                "url": url,
                "headers": build_anthropic_headers(config, url),
                "build_body": lambda messages, overrides=None: _anthropic_build_body(
                    url, model, config, messages, overrides, streaming, anthropic_budget_tokens
                ),
                "parse_stream": parse_anthropic_line,
                "parse_response": parse_anthropic_response,
                "streaming": streaming,
            }
        # Defense-in-depth against pasted /chat/completions tails.
        base = re.sub(r"/+$", "", custom_endpoint)
        if is_azure_openai_endpoint(base):
            url = build_azure_openai_url(
                base,
                model,
                config.get("azureApiVersion") or AZURE_OPENAI_API_VERSION,
            )
        elif re.search(r"/chat/completions$", base, re.IGNORECASE):
            url = base
        else:
            url = f"{base}/chat/completions"
        azure = is_azure_openai_endpoint(url)
        headers: dict[str, str] = {"Content-Type": JSON_CONTENT_TYPE}
        if api_key:
            if azure:
                headers["api-key"] = api_key
            else:
                headers["Authorization"] = f"Bearer {api_key}"
        if not azure and is_local_or_private_http_endpoint(url):
            headers.update(local_llm_origin_header())
        return {
            "url": url,
            "headers": merge_llm_request_headers(config.get("customHeaders"), headers),
            "build_body": lambda messages, overrides=None: {
                **build_openai_compatible_body(config, messages, overrides, streaming),
                **({"model": model} if not azure else {}),
            },
            "parse_stream": parse_openai_line,
            "parse_response": parse_openai_response,
            "streaming": streaming,
        }

    raise FsError(f"Unknown provider: {provider}")
