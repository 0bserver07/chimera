# tests/test_provider_ollama.py
from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.ollama import OllamaProvider
from chimera.types import Message


@pytest.fixture
def provider():
    with patch("chimera.providers.ollama.httpx") as mock_httpx:
        p = OllamaProvider(model="llama3.1", base_url="http://localhost:11434")
        yield p, mock_httpx


def test_complete_text_response(provider):
    prov, mock_httpx = provider

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": {"role": "assistant", "content": "Hello!"},
        "eval_count": 20,
        "prompt_eval_count": 100,
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
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "function": {
                    "name": "read_file",
                    "arguments": {"path": "main.py"},
                },
            }],
        },
        "eval_count": 30,
        "prompt_eval_count": 150,
    }
    mock_httpx.post.return_value = mock_response

    result = prov.complete([Message.user("Read main.py")])
    assert result.has_tool_calls is True
    assert result.tool_calls[0].name == "read_file"


def test_context_window(provider):
    prov, _ = provider
    assert prov.context_window > 0


def test_model_name(provider):
    prov, _ = provider
    assert prov.model_name == "llama3.1"
