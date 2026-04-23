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


# ---------------------------------------------------------------------------
# Regression tests for Ollama Anthropic-compatible endpoint routing.
#
# Ollama now serves an Anthropic-compatible API at http://localhost:11434.
# Users set ANTHROPIC_BASE_URL=http://localhost:11434 and expect models like
# "kimi-k2.6:cloud", "qwen3.5:cloud", "glm-5.1:cloud" to route through our
# anthropic provider, not the native Ollama provider.
# ---------------------------------------------------------------------------


def _clear_inference_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate _infer_provider from ambient env state."""
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_infer_kimi_no_env_routes_to_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    """kimi-* models default to anthropic (served via Anthropic-compat)."""
    _clear_inference_env(monkeypatch)
    assert _infer_provider("kimi-k2.6:cloud") == "anthropic"
    assert _infer_provider("moonshot-v1-32k") == "anthropic"


def test_infer_qwen_with_anthropic_base_url_routes_to_anthropic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ANTHROPIC_BASE_URL override beats qwen* prefix → anthropic (Ollama compat)."""
    _clear_inference_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:11434")
    assert _infer_provider("qwen3.5:cloud") == "anthropic"


def test_infer_qwen_no_env_falls_back_to_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the env override, qwen* still routes to native ollama."""
    _clear_inference_env(monkeypatch)
    assert _infer_provider("qwen3.5:cloud") == "ollama"


def test_infer_glm_no_env_routes_to_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    """glm-* always routes to anthropic (served via api.z.ai Anthropic-compat)."""
    _clear_inference_env(monkeypatch)
    assert _infer_provider("glm-5.1:cloud") == "anthropic"


def test_infer_gpt_with_anthropic_base_url_still_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gpt-* prefix wins over ANTHROPIC_BASE_URL (clearly not Anthropic)."""
    _clear_inference_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:11434")
    assert _infer_provider("gpt-4o") == "openai"
    assert _infer_provider("o3-mini") == "openai"
    assert _infer_provider("gemini-2.0-flash") == "google"


def test_infer_claude_with_anthropic_base_url_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """claude-* with ANTHROPIC_BASE_URL set still resolves to anthropic."""
    _clear_inference_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:11434")
    assert _infer_provider("claude-sonnet-4-20250514") == "anthropic"


def test_infer_anthropic_auth_token_alone_triggers_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ANTHROPIC_AUTH_TOKEN alone is enough to signal Anthropic-compat intent."""
    _clear_inference_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ollama")
    # A model that would normally route to ollama should now go to anthropic.
    assert _infer_provider("qwen3.5:cloud") == "anthropic"
    assert _infer_provider("llama3.1") == "anthropic"


def test_infer_unknown_model_error_mentions_ollama_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The error message should point users at the Ollama Anthropic-compat option."""
    _clear_inference_env(monkeypatch)
    with pytest.raises(ValueError, match="localhost:11434"):
        _infer_provider("some-unknown-model-xyz")
