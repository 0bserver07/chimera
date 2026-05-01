"""Tests for ``chimera.stoat.providers`` resolution chain.

Tests assert on the chain ordering and the friendly error rather than
constructing live providers (no SDK / no network).
"""

from __future__ import annotations

import argparse

import pytest

from chimera.stoat import providers as stoat_providers


def _ns(model: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(model=model)


def test_resolve_explicit_model_wins(monkeypatch) -> None:
    """``--model`` beats every env var."""
    monkeypatch.setenv("MOONSHOT_API_KEY", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    assert (
        stoat_providers._resolve_model(_ns("gpt-4o"))  # noqa: SLF001
        == "gpt-4o"
    )


def test_resolve_uses_stoat_model_env(monkeypatch) -> None:
    """``$STOAT_MODEL`` wins over provider keys when no ``--model``."""
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setenv("STOAT_MODEL", "kimi-k2-thinking")
    assert (
        stoat_providers._resolve_model(_ns())  # noqa: SLF001
        == "kimi-k2-thinking"
    )


def test_resolve_kimi_first_chain(monkeypatch) -> None:
    """``$MOONSHOT_API_KEY`` resolves to the Kimi default."""
    monkeypatch.delenv("STOAT_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setenv("MOONSHOT_API_KEY", "x")
    assert (
        stoat_providers._resolve_model(_ns())  # noqa: SLF001
        == stoat_providers._DEFAULT_KIMI_MODEL  # noqa: SLF001
    )


def test_resolve_anthropic_fallback(monkeypatch) -> None:
    """``$ANTHROPIC_API_KEY`` fires when Moonshot isn't set."""
    monkeypatch.delenv("STOAT_MODEL", raising=False)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert (
        stoat_providers._resolve_model(_ns())  # noqa: SLF001
        == stoat_providers._DEFAULT_ANTHROPIC_MODEL  # noqa: SLF001
    )


def test_resolve_no_keys_raises(monkeypatch) -> None:
    """No keys at all -> friendly ``ValueError``."""
    for var in (
        "STOAT_MODEL",
        "MOONSHOT_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "OLLAMA_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError) as exc_info:
        stoat_providers._resolve_model(_ns())  # noqa: SLF001
    msg = str(exc_info.value)
    assert "MOONSHOT_API_KEY" in msg
    assert "ANTHROPIC_API_KEY" in msg


def test_is_kimi_model_recognises_kimi_prefix() -> None:
    """``kimi-*`` ids route to the Moonshot path."""
    assert stoat_providers._is_kimi_model("kimi-k2.6") is True  # noqa: SLF001
    assert stoat_providers._is_kimi_model("KIMI-K2-Thinking") is True  # noqa: SLF001
    assert stoat_providers._is_kimi_model("gpt-4o") is False  # noqa: SLF001
    # OpenRouter-style ``moonshot/kimi-…`` stays false here — those route
    # via :func:`_should_use_openrouter`.
    assert stoat_providers._is_kimi_model("moonshot/kimi-k2.6") is False  # noqa: SLF001


def test_should_use_openrouter(monkeypatch) -> None:
    """``$OPENROUTER_API_KEY`` + ``vendor/name`` triggers OpenRouter."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    assert stoat_providers._should_use_openrouter("openai/gpt-4o") is True  # noqa: SLF001
    assert stoat_providers._should_use_openrouter("kimi-k2.6") is False  # noqa: SLF001
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert stoat_providers._should_use_openrouter("openai/gpt-4o") is False  # noqa: SLF001


def test_is_ollama_id() -> None:
    """``name:tag`` shape is treated as Ollama."""
    assert stoat_providers._is_ollama_id("qwen3.5:cloud") is True  # noqa: SLF001
    assert stoat_providers._is_ollama_id("kimi-k2.6") is False  # noqa: SLF001
    # OpenRouter wins for slash-shaped ids.
    assert stoat_providers._is_ollama_id("moonshot/kimi-k2.6") is False  # noqa: SLF001


def test_resolved_catalog_has_kimi_first() -> None:
    """The resolved catalog leads with the Kimi default."""
    catalog = stoat_providers.resolved_catalog()
    assert catalog[0][0] == stoat_providers._DEFAULT_KIMI_MODEL  # noqa: SLF001
    # Source describes Moonshot.
    assert "MOONSHOT_API_KEY" in catalog[0][1]


def test_format_catalog_emits_tab_rows() -> None:
    """``format_catalog`` produces TSV-style rows."""
    body = stoat_providers.format_catalog()
    assert body.endswith("\n")
    for line in body.strip().splitlines():
        assert "\t" in line


def test_build_provider_kimi_requires_moonshot_key(monkeypatch) -> None:
    """Selecting a kimi model without ``$MOONSHOT_API_KEY`` raises."""
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    with pytest.raises(ValueError) as exc_info:
        stoat_providers.build_provider(_ns("kimi-k2.6"))
    assert "MOONSHOT_API_KEY" in str(exc_info.value)


def test_build_provider_kimi_routes_through_compatible(monkeypatch) -> None:
    """The kimi path constructs an OpenAI-compatible provider."""
    monkeypatch.setenv("MOONSHOT_API_KEY", "test-key")
    captured: dict[str, object] = {}

    def fake_factory(*, provider_type=None, model=None, api_key=None, base_url=None, **kwargs):  # type: ignore[no-untyped-def]
        captured["provider_type"] = provider_type
        captured["model"] = model
        captured["api_key"] = api_key
        captured["base_url"] = base_url
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(
        "chimera.providers.factory.create_provider", fake_factory,
    )
    stoat_providers.build_provider(_ns("kimi-k2.6"))
    assert captured["provider_type"] == "compatible"
    assert captured["model"] == "kimi-k2.6"
    assert captured["api_key"] == "test-key"
    assert captured["base_url"] == stoat_providers._MOONSHOT_BASE_URL  # noqa: SLF001
