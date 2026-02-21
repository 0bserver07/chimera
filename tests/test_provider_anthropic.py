from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.anthropic import AnthropicProvider
from chimera.types import Message


@pytest.fixture
def provider():
    with patch("chimera.providers.anthropic.anthropic") as mock_mod:
        mock_client = MagicMock()
        mock_mod.Anthropic.return_value = mock_client
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="test-key")
        p._client = mock_client
        yield p, mock_client


def test_complete_text_response(provider):
    prov, mock_client = provider

    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text="Hello!")]
    mock_response.stop_reason = "end_turn"
    mock_response.usage.input_tokens = 100
    mock_response.usage.output_tokens = 20

    mock_client.messages.create.return_value = mock_response

    result = prov.complete([Message.user("Hi")])
    assert result.content == "Hello!"
    assert result.has_tool_calls is False
    assert result.usage["input_tokens"] == 100


def test_complete_tool_call(provider):
    prov, mock_client = provider

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "call_1"
    tool_block.name = "read_file"
    tool_block.input = {"path": "main.py"}

    mock_response = MagicMock()
    mock_response.content = [tool_block]
    mock_response.stop_reason = "tool_use"
    mock_response.usage.input_tokens = 150
    mock_response.usage.output_tokens = 30

    mock_client.messages.create.return_value = mock_response

    result = prov.complete([Message.user("Read main.py")])
    assert result.has_tool_calls is True
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].arguments == {"path": "main.py"}


def test_context_window(provider):
    prov, _ = provider
    assert prov.context_window > 0


def test_supports_tool_use(provider):
    prov, _ = provider
    assert prov.supports_tool_use is True


def test_model_name(provider):
    prov, _ = provider
    assert prov.model_name == "claude-sonnet-4-20250514"
