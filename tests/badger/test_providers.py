"""Tests for ``chimera.badger.providers`` — Anthropic-first provider chain."""

from __future__ import annotations

import argparse

import pytest

from chimera.badger import providers


def _clear_keys(monkeypatch) -> None:
    for var in (
        "BADGER_MODEL",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "XAI_API_KEY",
        "OLLAMA_HOST",
    ):
        monkeypatch.delenv(var, raising=False)


def test_explicit_model_wins(monkeypatch) -> None:
    _clear_keys(monkeypatch)
    args = argparse.Namespace(model="my-custom-model")
    assert providers._resolve_model(args) == "my-custom-model"  # noqa: SLF001


def test_env_model_fallback(monkeypatch) -> None:
    _clear_keys(monkeypatch)
    monkeypatch.setenv("BADGER_MODEL", "claude-opus-4-7")
    args = argparse.Namespace(model=None)
    assert providers._resolve_model(args) == "claude-opus-4-7"  # noqa: SLF001


def test_anthropic_first_in_chain(monkeypatch) -> None:
    """When ANTHROPIC_API_KEY and OPENAI_API_KEY are both set, Anthropic wins."""
    _clear_keys(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")
    args = argparse.Namespace(model=None)
    model = providers._resolve_model(args)  # noqa: SLF001
    assert model == providers._DEFAULT_ANTHROPIC_MODEL  # noqa: SLF001


def test_openai_when_no_anthropic(monkeypatch) -> None:
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")
    args = argparse.Namespace(model=None)
    model = providers._resolve_model(args)  # noqa: SLF001
    assert model == providers._DEFAULT_OPENAI_MODEL  # noqa: SLF001


def test_openrouter_when_only_one(monkeypatch) -> None:
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xxx")
    args = argparse.Namespace(model=None)
    model = providers._resolve_model(args)  # noqa: SLF001
    assert model == providers._DEFAULT_OPENROUTER_MODEL  # noqa: SLF001


def test_ollama_fallback(monkeypatch) -> None:
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    args = argparse.Namespace(model=None)
    model = providers._resolve_model(args)  # noqa: SLF001
    assert model == providers._DEFAULT_OLLAMA_MODEL  # noqa: SLF001


def test_no_keys_raises_friendly_value_error(monkeypatch) -> None:
    _clear_keys(monkeypatch)
    args = argparse.Namespace(model=None)
    with pytest.raises(ValueError) as exc:
        providers._resolve_model(args)  # noqa: SLF001
    msg = str(exc.value)
    assert "ANTHROPIC_API_KEY" in msg
    assert "OPENAI_API_KEY" in msg
    assert "OPENROUTER_API_KEY" in msg


def test_is_ollama_id() -> None:
    assert providers._is_ollama_id("qwen3:32b")  # noqa: SLF001
    assert providers._is_ollama_id("kimi-k2.6:cloud")  # noqa: SLF001
    assert not providers._is_ollama_id("anthropic/claude-sonnet-4-6")  # noqa: SLF001
    assert not providers._is_ollama_id("claude-sonnet-4-6")  # noqa: SLF001


def test_should_use_openrouter_requires_both_key_and_slash(monkeypatch) -> None:
    _clear_keys(monkeypatch)
    assert not providers._should_use_openrouter("anthropic/claude")  # noqa: SLF001
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xxx")
    assert providers._should_use_openrouter("anthropic/claude")  # noqa: SLF001
    assert not providers._should_use_openrouter("claude-sonnet-4-6")  # noqa: SLF001


def test_openrouter_extra_headers_default(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_REFERER", raising=False)
    monkeypatch.delenv("OPENROUTER_TITLE", raising=False)
    headers = providers._openrouter_extra_headers()  # noqa: SLF001
    assert "HTTP-Referer" in headers
    assert "X-Title" in headers
    assert headers["X-Title"] == "chimera badger 0.6.0"


def test_openrouter_extra_headers_env_override(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_REFERER", "https://example.com")
    monkeypatch.setenv("OPENROUTER_TITLE", "test-title")
    headers = providers._openrouter_extra_headers()  # noqa: SLF001
    assert headers["HTTP-Referer"] == "https://example.com"
    assert headers["X-Title"] == "test-title"


def test_build_provider_no_keys_raises(monkeypatch) -> None:
    _clear_keys(monkeypatch)
    args = argparse.Namespace(model=None)
    with pytest.raises(ValueError):
        providers.build_provider(args)
