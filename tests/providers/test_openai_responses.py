"""Tests for chimera.providers.openai_responses — OpenAI Responses API provider."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.openai_responses import OpenAIResponsesProvider
from chimera.providers.base import Response
from chimera.types import Message


class TestOpenAIResponsesProvider:

    def test_provider_constructs(self) -> None:
        """Provider can be instantiated with model and key."""
        provider = OpenAIResponsesProvider(
            model="gpt-4o",
            api_key="test-key",
            base_url="https://api.example.com",
        )
        assert provider._model == "gpt-4o"
        assert provider._api_key == "test-key"
        assert provider._base_url == "https://api.example.com"

    def test_model_name(self) -> None:
        """model_name property returns the configured model."""
        provider = OpenAIResponsesProvider(model="o3-mini", api_key="k")
        assert provider.model_name == "o3-mini"

    def test_format_messages_chat(self) -> None:
        """_format_messages_chat converts Message objects to chat dicts."""
        provider = OpenAIResponsesProvider(model="gpt-4o", api_key="k")
        messages = [
            Message.system("You are helpful."),
            Message.user("Hello"),
            Message.assistant("Hi there"),
        ]
        result = provider._format_messages_chat(messages)
        assert result == [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]

    def test_format_messages_responses(self) -> None:
        """_format_messages_responses converts Message objects to Responses API format."""
        provider = OpenAIResponsesProvider(model="gpt-4o", api_key="k")
        messages = [
            Message.system("sys"),
            Message.user("hi"),
            Message.assistant("hello"),
            Message.tool("call_123", "result text"),
        ]
        result = provider._format_messages_responses(messages)
        assert result[0] == {"role": "system", "content": "sys"}
        assert result[1] == {"role": "user", "content": "hi"}
        assert result[2] == {"role": "assistant", "content": "hello"}
        assert result[3] == {"type": "function_call_output", "call_id": "call_123", "output": "result text"}

    def test_parse_responses_result(self) -> None:
        """_parse_responses_result extracts content and tool calls from API data."""
        provider = OpenAIResponsesProvider(model="gpt-4o", api_key="k")
        data = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "Hello world"},
                    ],
                },
                {
                    "type": "function_call",
                    "call_id": "fc_1",
                    "name": "read_file",
                    "arguments": json.dumps({"path": "/tmp/test.py"}),
                },
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        resp = provider._parse_responses_result(data)
        assert isinstance(resp, Response)
        assert resp.content == "Hello world"
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "read_file"
        assert resp.tool_calls[0].arguments == {"path": "/tmp/test.py"}
        assert resp.tool_calls[0].id == "fc_1"
        assert resp.usage == {"input_tokens": 10, "output_tokens": 5}

    def test_fallback_to_chat_on_error(self) -> None:
        """When responses endpoint fails, provider falls back to chat completions."""
        provider = OpenAIResponsesProvider(model="gpt-4o", api_key="k")
        assert provider._use_responses_api is True

        # Mock the HTTP client
        mock_client = MagicMock()

        # First call (responses) raises an error
        mock_responses_resp = MagicMock()
        mock_responses_resp.raise_for_status.side_effect = Exception("404 Not Found")
        mock_client.post.side_effect = [
            mock_responses_resp,  # responses endpoint fails
            MagicMock(  # chat completions succeeds
                json=MagicMock(return_value={
                    "choices": [{
                        "message": {
                            "content": "fallback response",
                            "tool_calls": None,
                        },
                    }],
                    "usage": {"input_tokens": 5, "output_tokens": 3},
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        provider._client = mock_client

        messages = [Message.user("test")]
        resp = provider.complete(messages)

        assert resp.content == "fallback response"
        assert provider._use_responses_api is False

    def test_format_tools_responses(self) -> None:
        """_format_tools_responses formats tool schemas for Responses API."""
        provider = OpenAIResponsesProvider(model="gpt-4o", api_key="k")
        tools = [
            {"name": "read_file", "description": "Read a file", "parameters": {"type": "object"}},
        ]
        result = provider._format_tools_responses(tools)
        assert result == [
            {"type": "function", "name": "read_file", "description": "Read a file", "parameters": {"type": "object"}},
        ]

    def test_format_tools_chat(self) -> None:
        """_format_tools_chat formats tool schemas for Chat Completions API."""
        provider = OpenAIResponsesProvider(model="gpt-4o", api_key="k")
        tools = [
            {"name": "bash", "description": "Run bash", "parameters": {}},
        ]
        result = provider._format_tools_chat(tools)
        assert result == [
            {"type": "function", "function": {"name": "bash", "description": "Run bash", "parameters": {}}},
        ]

    def test_context_window(self) -> None:
        """context_window property returns a reasonable default."""
        provider = OpenAIResponsesProvider(model="gpt-4o", api_key="k")
        assert provider.context_window == 128_000

    def test_supports_tool_use(self) -> None:
        """supports_tool_use returns True."""
        provider = OpenAIResponsesProvider(model="gpt-4o", api_key="k")
        assert provider.supports_tool_use is True
