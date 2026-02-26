# tests/test_provider_modal.py
import os
from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.modal import ModalProvider
from chimera.types import Message


@pytest.fixture
def provider():
    with (
        patch("chimera.providers.modal.modal") as mock_modal,
        patch("chimera.providers.modal.httpx") as mock_httpx,
    ):
        p = ModalProvider(
            model="Qwen/Qwen3-235B-AWQ",
            base_url="https://test-modal-endpoint.modal.run/v1",
            token_id="test-token-id",
            token_secret="test-token-secret",
        )
        yield p, mock_httpx


def test_model_name(provider):
    prov, _ = provider
    assert prov.model_name == "Qwen/Qwen3-235B-AWQ"


def test_context_window(provider):
    prov, _ = provider
    assert prov.context_window == 131_072


def test_supports_tool_use(provider):
    prov, _ = provider
    assert prov.supports_tool_use is True


def test_complete_text_response(provider):
    prov, mock_httpx = provider

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{
            "message": {"role": "assistant", "content": "The answer is 42."},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }
    mock_httpx.post.return_value = mock_response

    result = prov.complete([Message.user("What is 6 * 7?")])
    assert result.content == "The answer is 42."
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
    assert result.tool_calls[0].arguments == {"path": "main.py"}


def test_env_vars_for_credentials():
    with (
        patch("chimera.providers.modal.modal") as mock_modal,
        patch("chimera.providers.modal.httpx") as mock_httpx,
        patch.dict(os.environ, {
            "MODAL_TOKEN_ID": "env-token-id",
            "MODAL_TOKEN_SECRET": "env-token-secret",
        }),
    ):
        p = ModalProvider(
            model="Qwen/Qwen3-235B-AWQ",
            base_url="https://test.modal.run/v1",
        )
        assert p._token_id == "env-token-id"
        assert p._token_secret == "env-token-secret"
