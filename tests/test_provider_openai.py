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
