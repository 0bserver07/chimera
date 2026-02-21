# tests/test_provider_google.py
from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.google import GoogleProvider
from chimera.types import Message


@pytest.fixture
def provider():
    with patch("chimera.providers.google.genai") as mock_mod:
        mock_model = MagicMock()
        mock_mod.GenerativeModel.return_value = mock_model
        mock_mod.configure = MagicMock()
        p = GoogleProvider(model="gemini-2.0-flash", api_key="test-key")
        p._model = mock_model
        yield p, mock_model


def test_complete_text_response(provider):
    prov, mock_model = provider

    mock_response = MagicMock()
    mock_response.text = "Hello from Gemini!"
    mock_candidate = MagicMock()
    mock_part = MagicMock()
    mock_part.text = "Hello from Gemini!"
    mock_part.function_call = None
    mock_candidate.content.parts = [mock_part]
    mock_candidate.finish_reason = 1  # STOP
    mock_response.candidates = [mock_candidate]
    mock_response.usage_metadata.prompt_token_count = 100
    mock_response.usage_metadata.candidates_token_count = 20

    mock_model.generate_content.return_value = mock_response

    result = prov.complete([Message.user("Hi")])
    assert result.content == "Hello from Gemini!"
    assert result.has_tool_calls is False


def test_complete_tool_call(provider):
    prov, mock_model = provider

    mock_fc = MagicMock()
    mock_fc.name = "read_file"
    mock_fc.args = {"path": "main.py"}

    mock_part = MagicMock()
    mock_part.text = None
    mock_part.function_call = mock_fc

    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]
    mock_candidate.finish_reason = 1
    mock_response = MagicMock()
    mock_response.candidates = [mock_candidate]
    mock_response.usage_metadata.prompt_token_count = 150
    mock_response.usage_metadata.candidates_token_count = 30

    mock_model.generate_content.return_value = mock_response

    result = prov.complete([Message.user("Read main.py")])
    assert result.has_tool_calls is True
    assert result.tool_calls[0].name == "read_file"


def test_context_window(provider):
    prov, _ = provider
    assert prov.context_window > 0


def test_model_name(provider):
    prov, _ = provider
    assert prov.model_name == "gemini-2.0-flash"
