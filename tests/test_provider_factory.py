# tests/test_provider_factory.py
from unittest.mock import patch, MagicMock

import pytest

from chimera.providers.factory import _infer_provider, create_provider


def test_create_anthropic():
    with patch("chimera.providers.anthropic.anthropic") as mock:
        mock.Anthropic.return_value = MagicMock()
        p = create_provider("anthropic", model="claude-sonnet-4-20250514", api_key="test")
        assert p.model_name == "claude-sonnet-4-20250514"


def test_create_openai():
    with patch("chimera.providers.openai.openai") as mock:
        mock.OpenAI.return_value = MagicMock()
        p = create_provider("openai", model="gpt-4o", api_key="test")
        assert p.model_name == "gpt-4o"


def test_create_google():
    with patch("chimera.providers.google.genai") as mock:
        mock.GenerativeModel.return_value = MagicMock()
        p = create_provider("google", model="gemini-2.0-flash", api_key="test")
        assert p.model_name == "gemini-2.0-flash"


def test_create_ollama():
    with patch("chimera.providers.ollama.httpx"):
        p = create_provider("ollama", model="llama3.1")
        assert p.model_name == "llama3.1"


def test_create_unknown_raises():
    with pytest.raises(ValueError, match="Unknown provider"):
        create_provider("unknown_provider", model="foo")


def test_create_from_model_string():
    """Infer provider from model name pattern."""
    with patch("chimera.providers.anthropic.anthropic") as mock:
        mock.Anthropic.return_value = MagicMock()
        p = create_provider(model="claude-sonnet-4-20250514", api_key="test")
        assert p.model_name == "claude-sonnet-4-20250514"

    with patch("chimera.providers.openai.openai") as mock:
        mock.OpenAI.return_value = MagicMock()
        p = create_provider(model="gpt-4o", api_key="test")
        assert p.model_name == "gpt-4o"


def test_infer_provider_glm_routes_to_anthropic():
    """GLM-5 (served via Anthropic-compatible api.z.ai) must infer as anthropic.

    Regression test: ``CodingAgent(model="glm-5")`` previously crashed with
    ``Cannot infer provider from model name 'glm-5'``.
    """
    assert _infer_provider("glm-5") == "anthropic"
    assert _infer_provider("GLM-5") == "anthropic"
    assert _infer_provider("glm-4.6") == "anthropic"


def test_infer_provider_known_prefixes_unchanged():
    """Existing prefix inference must not regress."""
    assert _infer_provider("claude-sonnet-4-20250514") == "anthropic"
    assert _infer_provider("gpt-4o") == "openai"
    assert _infer_provider("o3-mini") == "openai"
    assert _infer_provider("gemini-2.0-flash") == "google"
    assert _infer_provider("llama3.1") == "ollama"
