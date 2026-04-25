"""Tests for prompt caching and extended thinking in AnthropicProvider."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chimera.providers.anthropic import AnthropicProvider
from chimera.types import Message


@pytest.fixture
def _patch_client(monkeypatch):
    """Patch the Anthropic client constructor so no real API key is needed."""
    monkeypatch.setattr(
        "chimera.providers.anthropic.anthropic.Anthropic",
        lambda **kw: MagicMock(),
    )


# -- Prompt Caching Tests --

class TestPromptCaching:

    def test_cache_disabled_sends_plain_system(self, _patch_client):
        provider = AnthropicProvider(model="test-model", enable_cache=False)
        msgs = [Message.system("You are helpful."), Message.user("Hi")]
        kwargs = provider._prepare_request(msgs)
        assert kwargs["system"] == "You are helpful."

    def test_cache_enabled_wraps_system_with_cache_control(self, _patch_client):
        provider = AnthropicProvider(model="test-model", enable_cache=True)
        msgs = [Message.system("You are helpful."), Message.user("Hi")]
        kwargs = provider._prepare_request(msgs)
        assert isinstance(kwargs["system"], list)
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert kwargs["system"][0]["text"] == "You are helpful."

    def test_cache_enabled_marks_last_tool(self, _patch_client):
        provider = AnthropicProvider(model="test-model", enable_cache=True)
        msgs = [Message.user("Hi")]
        tools = [
            {"name": "read", "description": "Read a file", "input_schema": {}},
            {"name": "write", "description": "Write a file", "input_schema": {}},
        ]
        kwargs = provider._prepare_request(msgs, tools=tools)
        assert "cache_control" not in kwargs["tools"][0]
        assert kwargs["tools"][1]["cache_control"] == {"type": "ephemeral"}

    def test_cache_disabled_no_tool_cache_control(self, _patch_client):
        provider = AnthropicProvider(model="test-model", enable_cache=False)
        msgs = [Message.user("Hi")]
        tools = [{"name": "read", "description": "Read", "input_schema": {}}]
        kwargs = provider._prepare_request(msgs, tools=tools)
        assert "cache_control" not in kwargs["tools"][0]

    def test_parse_response_extracts_cache_tokens(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Hello")]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 10
        mock_response.usage.cache_creation_input_tokens = 80
        mock_response.usage.cache_read_input_tokens = 20
        result = AnthropicProvider._parse_response(mock_response)
        assert result.usage["cache_creation_input_tokens"] == 80
        assert result.usage["cache_read_input_tokens"] == 20

    def test_parse_response_no_cache_tokens_when_absent(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Hello")]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 10
        del mock_response.usage.cache_creation_input_tokens
        del mock_response.usage.cache_read_input_tokens
        result = AnthropicProvider._parse_response(mock_response)
        assert "cache_creation_input_tokens" not in result.usage


# -- Extended Thinking Tests --

class TestExtendedThinking:

    def test_thinking_disabled_uses_provided_temperature(self, _patch_client):
        provider = AnthropicProvider(model="test-model", enable_thinking=False)
        msgs = [Message.user("Hi")]
        kwargs = provider._prepare_request(msgs, temperature=0.5)
        assert kwargs["temperature"] == 0.5
        assert "thinking" not in kwargs

    def test_thinking_enabled_forces_temp_1_and_adds_budget(self, _patch_client):
        provider = AnthropicProvider(
            model="test-model", enable_thinking=True, thinking_budget=5000,
        )
        msgs = [Message.user("Hi")]
        kwargs = provider._prepare_request(msgs, temperature=0.0)
        assert kwargs["temperature"] == 1
        assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 5000}

    def test_thinking_default_budget(self, _patch_client):
        provider = AnthropicProvider(model="test-model", enable_thinking=True)
        kwargs = provider._prepare_request([Message.user("Hi")])
        assert kwargs["thinking"]["budget_tokens"] == 10_000

    def test_parse_response_extracts_thinking_block(self):
        mock_response = MagicMock()
        thinking_block = MagicMock(type="thinking", thinking="Let me reason step by step")
        text_block = MagicMock(type="text", text="The answer is 42.")
        mock_response.content = [thinking_block, text_block]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50
        del mock_response.usage.cache_creation_input_tokens
        del mock_response.usage.cache_read_input_tokens

        result = AnthropicProvider._parse_response(mock_response)
        assert result.content == "The answer is 42."
        assert "thinking_tokens" in result.usage

    def test_thinking_and_cache_together(self, _patch_client):
        provider = AnthropicProvider(
            model="test-model",
            enable_cache=True,
            enable_thinking=True,
            thinking_budget=8000,
        )
        msgs = [Message.system("System prompt"), Message.user("Think about this")]
        tools = [{"name": "bash", "description": "Run command", "input_schema": {}}]
        kwargs = provider._prepare_request(msgs, tools=tools)

        # Cache on system
        assert isinstance(kwargs["system"], list)
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        # Cache on tools
        assert kwargs["tools"][0]["cache_control"] == {"type": "ephemeral"}
        # Thinking enabled
        assert kwargs["thinking"]["budget_tokens"] == 8000
        assert kwargs["temperature"] == 1
