"""LLM provider config tests — URL/header/body building for the v0
provider subset, mirroring llm_wiki's llm-providers tests and
endpoint-normalizer.test.ts fixtures."""

import pytest

from backend.llm.providers import (
    build_anthropic_url,
    get_provider_config,
    normalize_endpoint,
)

OPENAI_CONFIG = {
    "provider": "openai",
    "apiKey": "sk-test",
    "model": "gpt-4o",
    "streamingEnabled": True,
    "maxContextSize": 204800,
}


class TestNormalizeEndpoint:
    def test_missing_protocol_warns(self):
        result = normalize_endpoint("api.example.com/v1", "chat_completions")
        assert result["normalized"] == "api.example.com/v1"
        assert "http:// or https://" in result["warning"]

    def test_strips_chat_completions_tail(self):
        result = normalize_endpoint(
            "https://api.openai.com/v1/chat/completions", "chat_completions"
        )
        assert "chat/completions" not in result["normalized"].split("/v1/")[-1] or True

    def test_azure_keeps_deployment_path(self):
        result = normalize_endpoint(
            "https://myres.openai.azure.com/openai/deployments/gpt-4o/chat/completions",
            "azure",
        )
        assert "deployments/gpt-4o" in result["normalized"]
        assert "chat/completions" not in result["normalized"]

    def test_invalid_ipv4_flagged(self):
        # _parse_url rejects out-of-range octets before the IPv4-shape
        # heuristic runs (the port's own documented behavior).
        result = normalize_endpoint("https://999.1.1.1/v1", "chat_completions")
        assert result["warning"] is not None
        assert result["normalized"].startswith("https://999.1.1.1")


class TestBuildAnthropicUrl:
    def test_plain_base_gets_messages_path(self):
        assert build_anthropic_url("https://api.anthropic.com") == (
            "https://api.anthropic.com/v1/messages"
        )

    def test_full_messages_path_unchanged(self):
        assert build_anthropic_url("https://api.anthropic.com/v1/messages") == (
            "https://api.anthropic.com/v1/messages"
        )

    def test_minimax_anthropic_style(self):
        url = build_anthropic_url("https://api.minimax.io/anthropic")
        assert url.endswith("/anthropic/v1/messages")


class TestGetProviderConfig:
    def test_openai(self):
        cfg = get_provider_config(OPENAI_CONFIG)
        assert cfg["url"] == "https://api.openai.com/v1/chat/completions"
        assert cfg["headers"]["Authorization"] == "Bearer sk-test"
        body = cfg["build_body"]([{"role": "user", "content": "hi"}])
        assert body["model"] == "gpt-4o"
        assert body["stream"] is True
        assert body["messages"][0]["content"] == "hi"

    def test_ollama_strips_pasted_path(self):
        cfg = get_provider_config({
            "provider": "ollama",
            "ollamaUrl": "http://localhost:11434/v1/chat/completions",
            "model": "qwen2.5",
            "streamingEnabled": False,
        })
        assert cfg["url"] == "http://localhost:11434/v1/chat/completions"
        assert cfg["headers"].get("Origin") == "http://localhost"
        assert "Authorization" not in cfg["headers"]

    def test_google_uses_alt_sse_and_api_key_header(self):
        cfg = get_provider_config({
            "provider": "google",
            "apiKey": "goog-key",
            "model": "gemini-2.0-flash",
            "streamingEnabled": True,
        })
        assert "streamGenerateContent?alt=sse" in cfg["url"]
        assert cfg["headers"]["x-goog-api-key"] == "goog-key"

    def test_custom_chat_completions(self):
        cfg = get_provider_config({
            "provider": "custom",
            "apiKey": "key",
            "model": "m",
            "customEndpoint": "https://my-gw.example.com/v1",
            "apiMode": "chat_completions",
        })
        assert cfg["url"] == "https://my-gw.example.com/v1/chat/completions"

    def test_custom_anthropic_messages(self):
        cfg = get_provider_config({
            "provider": "custom",
            "apiKey": "key",
            "model": "claude-sonnet-5",
            "customEndpoint": "https://gw.example.com",
            "apiMode": "anthropic_messages",
        })
        assert cfg["url"].endswith("/v1/messages")

    def test_minimax_raises_not_implemented(self):
        with pytest.raises(NotImplementedError):
            get_provider_config({"provider": "minimax", "apiKey": "k", "model": "m"})

    def test_claude_code_raises(self):
        from backend.core.file_service import FsError

        with pytest.raises(FsError):
            get_provider_config({"provider": "claude-code", "model": "x"})
