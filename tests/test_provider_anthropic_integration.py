"""Real integration tests for Anthropic-compatible providers.

Configure via environment variables:

    export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
    export ANTHROPIC_AUTH_TOKEN="your-token-here"
    export ANTHROPIC_MODEL="glm-5"

Or for direct Anthropic:

    export ANTHROPIC_API_KEY="sk-ant-..."
    export ANTHROPIC_MODEL="claude-sonnet-4-20250514"

Skipped when no credentials are set.
"""
from __future__ import annotations

import os

import pytest

from chimera.providers.anthropic import AnthropicProvider
from chimera.types import Message

# Skip entire module if no credentials
_api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
_model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

pytestmark = pytest.mark.skipif(
    not _api_key,
    reason="Set ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN to run integration tests",
)


@pytest.fixture(scope="module")
def provider() -> AnthropicProvider:
    """Create a real provider from environment variables."""
    return AnthropicProvider(
        model=_model,
        api_key=_api_key,
        base_url=os.environ.get("ANTHROPIC_BASE_URL"),
    )


class TestTextCompletion:
    def test_simple_response(self, provider: AnthropicProvider):
        """Provider returns a text response."""
        result = provider.complete([Message.user("What is 2+2? Reply with just the number.")])
        assert result.content
        assert "4" in result.content

    def test_usage_tracking(self, provider: AnthropicProvider):
        """Provider reports token usage."""
        result = provider.complete([Message.user("Say hello.")])
        assert result.usage["input_tokens"] > 0
        assert result.usage["output_tokens"] > 0

    def test_system_message(self, provider: AnthropicProvider):
        """Provider handles system messages."""
        messages = [
            Message.system("You are a pirate. Always say 'arr'."),
            Message.user("Greet me."),
        ]
        result = provider.complete(messages)
        assert result.content
        assert len(result.content) > 0

    def test_multi_turn(self, provider: AnthropicProvider):
        """Provider handles multi-turn conversations."""
        messages = [
            Message.user("My name is Zephyr. Remember it."),
            Message.assistant("Got it, your name is Zephyr."),
            Message.user("What is my name?"),
        ]
        result = provider.complete(messages)
        assert "Zephyr" in result.content

    def test_temperature(self, provider: AnthropicProvider):
        """Provider accepts temperature parameter."""
        result = provider.complete(
            [Message.user("Say exactly: 'test'")],
            temperature=0.0,
        )
        assert result.content


class TestToolUse:
    CALCULATOR_TOOL = {
        "name": "calculator",
        "description": "Evaluate a math expression.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Math expression to evaluate, e.g. '2 + 2'",
                },
            },
            "required": ["expression"],
        },
    }

    def test_tool_call(self, provider: AnthropicProvider):
        """Provider returns tool calls when tools are available."""
        result = provider.complete(
            [Message.user("What is 137 * 42? Use the calculator.")],
            tools=[self.CALCULATOR_TOOL],
        )
        assert result.has_tool_calls
        assert len(result.tool_calls) >= 1
        tc = result.tool_calls[0]
        assert tc.name == "calculator"
        assert "expression" in tc.arguments

    def test_tool_result_roundtrip(self, provider: AnthropicProvider):
        """Provider handles tool call → tool result → final answer."""
        # Step 1: Get tool call
        result = provider.complete(
            [Message.user("What is 137 * 42? Use the calculator.")],
            tools=[self.CALCULATOR_TOOL],
        )
        assert result.has_tool_calls
        tc = result.tool_calls[0]

        # Step 2: Send tool result back
        messages = [
            Message.user("What is 137 * 42? Use the calculator."),
            Message.assistant("", tool_calls=[tc]),
            Message.tool(call_id=tc.id, content=str(137 * 42)),
        ]
        final = provider.complete(messages, tools=[self.CALCULATOR_TOOL])
        # Models may format with commas: "5,754" vs "5754"
        assert "5754" in final.content or "5,754" in final.content

    def test_no_tool_call_when_unnecessary(self, provider: AnthropicProvider):
        """Provider doesn't force tool use for simple questions."""
        result = provider.complete(
            [Message.user("What color is the sky?")],
            tools=[self.CALCULATOR_TOOL],
        )
        # Should answer directly without using calculator
        assert result.content
        assert len(result.content) > 0


class TestEdgeCases:
    def test_empty_assistant_content(self, provider: AnthropicProvider):
        """Provider handles assistant messages with empty content (tool-only)."""
        tool = {
            "name": "get_weather",
            "description": "Get current weather for a city.",
            "input_schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }
        result = provider.complete(
            [Message.user("What's the weather in Tokyo?")],
            tools=[tool],
        )
        # Should either call tool or answer — both are valid
        assert result.content or result.has_tool_calls

    def test_long_context(self, provider: AnthropicProvider):
        """Provider handles longer inputs without error."""
        padding = "The quick brown fox jumps over the lazy dog. " * 100
        result = provider.complete([
            Message.user(f"Here is some text: {padding}\n\nHow many words approximately?"),
        ])
        assert result.content

    def test_model_name_property(self, provider: AnthropicProvider):
        """Provider reports correct model name."""
        assert provider.model_name == _model

    def test_supports_tool_use_property(self, provider: AnthropicProvider):
        """Provider reports tool use support."""
        assert provider.supports_tool_use is True
