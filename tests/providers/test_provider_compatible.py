# tests/test_provider_compatible.py
from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.compatible import OpenAICompatibleProvider
from chimera.types import Message


@pytest.fixture
def provider():
    with patch("chimera.providers.compatible.httpx") as mock_httpx:
        p = OpenAICompatibleProvider(
            model="deepseek-r1",
            base_url="https://api.openrouter.ai/v1",
            api_key="test-key",
        )
        yield p, mock_httpx


def test_complete_text_response(provider):
    prov, mock_httpx = provider

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{
            "message": {"role": "assistant", "content": "Hello!"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }
    mock_httpx.post.return_value = mock_response

    result = prov.complete([Message.user("Hi")])
    assert result.content == "Hello!"
    assert result.has_tool_calls is False


def test_complete_tool_call(provider):
    prov, mock_httpx = provider

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "main.py"}'},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 150, "completion_tokens": 30},
    }
    mock_httpx.post.return_value = mock_response

    result = prov.complete([Message.user("Read main.py")])
    assert result.has_tool_calls is True
    assert result.tool_calls[0].name == "read_file"


def test_custom_headers(provider):
    prov, mock_httpx = provider
    prov._headers["X-Custom"] = "value"

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    mock_httpx.post.return_value = mock_response

    prov.complete([Message.user("Hi")])
    call_args = mock_httpx.post.call_args
    assert "X-Custom" in call_args[1]["headers"]


def test_model_name(provider):
    prov, _ = provider
    assert prov.model_name == "deepseek-r1"
