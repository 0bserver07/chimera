from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.anthropic import AnthropicProvider, _parse_model_suffix
from chimera.types import Message


@pytest.fixture
def provider():
    with patch("chimera.providers.anthropic.anthropic") as mock_mod:
        mock_client = MagicMock()
        mock_mod.Anthropic.return_value = mock_client
        p = AnthropicProvider(model="claude-sonnet-4-20250514", api_key="test-key")
        p._client = mock_client
        yield p, mock_client


def test_parse_model_suffix_extracts_window():
    assert _parse_model_suffix("glm-5.2[1m]") == ("glm-5.2", 1_000_000)
    assert _parse_model_suffix("claude-sonnet-4[200k]") == ("claude-sonnet-4", 200_000)
    assert _parse_model_suffix("glm-5.2[1.5m]") == ("glm-5.2", 1_500_000)
    assert _parse_model_suffix("kimi-k2[256K]") == ("kimi-k2", 256_000)


def test_parse_model_suffix_no_suffix_is_identity():
    assert _parse_model_suffix("glm-5.2") == ("glm-5.2", None)
    assert _parse_model_suffix("claude-sonnet-4-20250514") == (
        "claude-sonnet-4-20250514",
        None,
    )


def test_context_window_honors_suffix_override():
    with patch("chimera.providers.anthropic.anthropic") as mock_mod:
        mock_mod.Anthropic.return_value = MagicMock()
        p = AnthropicProvider(model="glm-5.2[1m]", api_key="test-key")
        # Suffix stripped from the wire id, kept as the declared window.
        assert p.model_name == "glm-5.2"
        assert p.context_window == 1_000_000


def test_request_uses_stripped_model_id():
    """Regression: z.ai 400s on 'glm-5.2[1m]'; the wire id must be 'glm-5.2'."""
    with patch("chimera.providers.anthropic.anthropic") as mock_mod:
        mock_client = MagicMock()
        mock_mod.Anthropic.return_value = mock_client
        p = AnthropicProvider(model="glm-5.2[1m]", api_key="test-key")
        p._client = mock_client

        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="pong")]
        mock_response.stop_reason = "end_turn"
        mock_response.usage.input_tokens = 1
        mock_response.usage.output_tokens = 1
        mock_client.messages.create.return_value = mock_response

        p.complete([Message.user("hi")])
        sent_model = mock_client.messages.create.call_args.kwargs["model"]
        assert sent_model == "glm-5.2"


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
