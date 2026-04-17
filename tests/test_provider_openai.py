# tests/test_provider_openai.py
from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.openai import OpenAIProvider
from chimera.types import Message


@pytest.fixture
def provider():
    with patch("chimera.providers.openai.openai") as mock_mod:
        mock_client = MagicMock()
        mock_mod.OpenAI.return_value = mock_client
        p = OpenAIProvider(model="gpt-4o", api_key="test-key")
        p._client = mock_client
        yield p, mock_client


def test_complete_text_response(provider):
    prov, mock_client = provider

    mock_choice = MagicMock()
    mock_choice.message.content = "Hello!"
    mock_choice.message.tool_calls = None
    mock_choice.finish_reason = "stop"

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage.prompt_tokens = 100
    mock_response.usage.completion_tokens = 20

    mock_client.chat.completions.create.return_value = mock_response

    result = prov.complete([Message.user("Hi")])
    assert result.content == "Hello!"
    assert result.has_tool_calls is False
    assert result.usage["input_tokens"] == 100


def test_complete_tool_call(provider):
    prov, mock_client = provider

    mock_tc = MagicMock()
    mock_tc.id = "call_1"
    mock_tc.function.name = "read_file"
    mock_tc.function.arguments = '{"path": "main.py"}'

    mock_choice = MagicMock()
    mock_choice.message.content = None
    mock_choice.message.tool_calls = [mock_tc]
    mock_choice.finish_reason = "tool_calls"

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage.prompt_tokens = 150
    mock_response.usage.completion_tokens = 30

    mock_client.chat.completions.create.return_value = mock_response

    result = prov.complete([Message.user("Read main.py")])
    assert result.has_tool_calls is True
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].arguments == {"path": "main.py"}


def test_system_message_handling(provider):
    prov, mock_client = provider

    mock_choice = MagicMock()
    mock_choice.message.content = "I'm an AI."
    mock_choice.message.tool_calls = None
    mock_choice.finish_reason = "stop"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage.prompt_tokens = 50
    mock_response.usage.completion_tokens = 10
    mock_client.chat.completions.create.return_value = mock_response

    prov.complete([Message.system("You are helpful"), Message.user("Hi")])
    call_args = mock_client.chat.completions.create.call_args
    messages = call_args[1]["messages"]
    assert messages[0]["role"] == "system"


def test_context_window(provider):
    prov, _ = provider
    assert prov.context_window > 0


def test_model_name(provider):
    prov, _ = provider
    assert prov.model_name == "gpt-4o"


def test_extract_usage_reasoning_and_cache_tokens():
    """OpenAI reasoning (o1/o3) and cache tokens are surfaced in Response.usage."""

    class _UsageObj:
        prompt_tokens = 1000
        completion_tokens = 500
        class completion_tokens_details:  # noqa: N801
            reasoning_tokens = 150
        class prompt_tokens_details:  # noqa: N801
            cached_tokens = 800

    usage = OpenAIProvider._extract_usage(_UsageObj)
    assert usage["input_tokens"] == 1000
    assert usage["output_tokens"] == 500
    assert usage["reasoning_tokens"] == 150
    assert usage["cache_read_input_tokens"] == 800


def test_extract_usage_without_details():
    """When the SDK returns no details objects, only basic tokens are set."""

    class _UsageObj:
        prompt_tokens = 100
        completion_tokens = 20
        completion_tokens_details = None
        prompt_tokens_details = None

    usage = OpenAIProvider._extract_usage(_UsageObj)
    assert usage == {"input_tokens": 100, "output_tokens": 20}
