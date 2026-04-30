"""Tests for ``chimera.weasel.providers`` (agent W5).

Covers the env-var-controlled provider chain:

1. Explicit ``args.model`` wins over everything.
2. ``$WEASEL_MODEL`` wins over default-by-env-var.
3. ``$OPENAI_API_KEY`` -> ``gpt-4o``.
4. ``$ANTHROPIC_API_KEY`` -> ``claude-sonnet-4-6``.
5. ``$OPENROUTER_API_KEY`` -> ``openai/gpt-4o`` via the ``compatible``
   provider against ``openrouter.ai`` (when the model id contains ``/``).
6. ``$LLAMACPP_API_KEY`` -> default model via the ``compatible`` provider
   against ``127.0.0.1:8888``.
7. ``$OLLAMA_API_KEY`` -> default Ollama tag via :class:`OllamaProvider`.
8. Friendly :class:`ValueError` when no model + no env vars.

Plus:

* Ollama tag detection (``name:tag`` shape) routes through
  :class:`OllamaProvider`, mirroring otter.
* ``--list-models`` / :func:`format_catalog` output exposes every chain
  step.
* ``args.max_tokens`` / ``args.no_color`` plumbing parity with otter.

Tests stub :func:`chimera.providers.factory.create_provider` and
:class:`chimera.providers.ollama.OllamaProvider` so we never hit real
SDKs / network. Each test isolates env vars via ``monkeypatch``.
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from chimera.weasel import providers as weasel_providers


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _StubProvider:
    """Minimal stand-in for :class:`chimera.providers.base.Provider`."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.model_name = kwargs.get("model", "")


def _ns(**overrides: Any) -> argparse.Namespace:
    """Build a default-ish weasel argparse namespace for tests.

    We default ``model=None`` so the env-var chain is the path under test;
    callers override per-test.
    """
    base: dict[str, Any] = {"model": None, "no_color": False}
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture
def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every weasel-relevant env var so tests start clean."""
    for var in (
        "WEASEL_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "LLAMACPP_API_KEY",
        "OLLAMA_API_KEY",
        "OLLAMA_HOST",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def capture_factory(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace :func:`create_provider` with a capturing stub."""
    record: dict[str, Any] = {"calls": []}

    def _stub(**kwargs: Any) -> _StubProvider:
        record["calls"].append(kwargs)
        return _StubProvider(**kwargs)

    monkeypatch.setattr(
        "chimera.providers.factory.create_provider",
        _stub,
    )
    return record


@pytest.fixture
def capture_ollama(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace :class:`OllamaProvider` with a capturing stub."""
    record: dict[str, Any] = {"calls": []}

    class _StubOllama:
        def __init__(self, **kwargs: Any) -> None:
            record["calls"].append(kwargs)
            self.kwargs = kwargs
            self.model_name = kwargs.get("model", "")

    monkeypatch.setattr(
        "chimera.providers.ollama.OllamaProvider",
        _StubOllama,
    )
    return record


# ---------------------------------------------------------------------------
# Resolution chain — happy paths
# ---------------------------------------------------------------------------


def test_explicit_model_wins(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``args.model`` overrides every env var."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")
    monkeypatch.setenv("WEASEL_MODEL", "gpt-4o-mini")

    weasel_providers.build_provider(_ns(model="gpt-4o"))

    assert capture_factory["calls"][0]["model"] == "gpt-4o"


def test_weasel_model_env_wins_over_default(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$WEASEL_MODEL`` wins over the env-var-derived default."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")
    monkeypatch.setenv("WEASEL_MODEL", "gpt-4o-mini")

    weasel_providers.build_provider(_ns())

    assert capture_factory["calls"][0]["model"] == "gpt-4o-mini"


def test_default_openai_when_only_openai_key_set(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ``$OPENAI_API_KEY`` set -> default to ``gpt-4o``."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")

    weasel_providers.build_provider(_ns())

    call = capture_factory["calls"][0]
    assert call["model"] == "gpt-4o"
    # Bare OpenAI must NOT be routed through openrouter / compatible.
    assert "provider_type" not in call


def test_default_anthropic_when_only_anthropic_key_set(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ``$ANTHROPIC_API_KEY`` -> default to ``claude-sonnet-4-6``."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")

    weasel_providers.build_provider(_ns())

    assert capture_factory["calls"][0]["model"] == "claude-sonnet-4-6"


def test_openai_takes_priority_over_anthropic(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both keys set -> OpenAI wins (per spec ordering: OpenAI > Anthropic).

    Documents weasel's chain order, which differs from otter (which
    prefers Anthropic). Users wanting Anthropic explicitly can pass
    ``--model claude-sonnet-4-6`` or set ``$WEASEL_MODEL``.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")

    weasel_providers.build_provider(_ns())

    assert capture_factory["calls"][0]["model"] == "gpt-4o"


def test_default_openrouter_when_only_openrouter_key_set(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ``$OPENROUTER_API_KEY`` -> route via ``compatible`` provider."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xxx")

    weasel_providers.build_provider(_ns())

    call = capture_factory["calls"][0]
    assert call["provider_type"] == "compatible"
    assert call["model"] == "openai/gpt-4o"
    assert call["api_key"] == "sk-or-xxx"
    assert call["base_url"] == "https://openrouter.ai/api/v1"


def test_openrouter_routes_when_model_has_slash(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$OPENROUTER_API_KEY`` + slash-shaped model -> compatible route."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xxx")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")

    weasel_providers.build_provider(_ns(model="anthropic/claude-sonnet-4"))

    call = capture_factory["calls"][0]
    assert call["provider_type"] == "compatible"
    assert call["model"] == "anthropic/claude-sonnet-4"
    assert call["base_url"] == "https://openrouter.ai/api/v1"


def test_openrouter_skipped_when_model_lacks_slash(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare model id is not hijacked even when an OpenRouter key is set."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xxx")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")

    weasel_providers.build_provider(_ns(model="gpt-4o"))

    call = capture_factory["calls"][0]
    # When OPENAI_API_KEY is present we want the bare model to fall
    # through to the regular factory inference, NOT openrouter.
    assert call.get("provider_type") is None or call["provider_type"] != "compatible"
    assert call["model"] == "gpt-4o"


def test_default_llamacpp_when_only_llamacpp_key_set(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ``$LLAMACPP_API_KEY`` -> compatible provider @ 127.0.0.1:8888."""
    monkeypatch.setenv("LLAMACPP_API_KEY", "lc-xxx")

    weasel_providers.build_provider(_ns())

    call = capture_factory["calls"][0]
    assert call["provider_type"] == "compatible"
    assert call["model"] == "gpt-oss"
    assert call["api_key"] == "lc-xxx"
    assert call["base_url"] == "http://127.0.0.1:8888/v1"


def test_llamacpp_skipped_when_explicit_model_differs(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit ``--model gpt-4o`` doesn't get hijacked by stray llama.cpp.

    Mirrors the openrouter-no-slash behaviour: llama.cpp routing only
    kicks in when the chain landed on the llama.cpp default.
    """
    monkeypatch.setenv("LLAMACPP_API_KEY", "lc-xxx")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")

    weasel_providers.build_provider(_ns(model="gpt-4o"))

    call = capture_factory["calls"][0]
    assert call.get("provider_type") != "compatible"
    assert call["model"] == "gpt-4o"


def test_default_ollama_when_only_ollama_key_set(
    clear_env: None,
    capture_ollama: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ``$OLLAMA_API_KEY`` -> route via OllamaProvider @ 127.0.0.1:11434."""
    monkeypatch.setenv("OLLAMA_API_KEY", "ol-xxx")

    weasel_providers.build_provider(_ns())

    assert len(capture_ollama["calls"]) == 1
    call = capture_ollama["calls"][0]
    assert call["model"] == "qwen3.5:cloud"
    assert call["base_url"] == "http://127.0.0.1:11434"
    # ``:cloud`` tag should bump context to 262k per the otter heuristic.
    assert call["context_length"] == 262_144


def test_ollama_tag_detection_routes_through_ollama_provider(
    clear_env: None,
    capture_ollama: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--model name:tag`` always routes via :class:`OllamaProvider`.

    Mirrors otter's tag detection: the colon-tagged shape unambiguously
    points at the local Ollama daemon, regardless of what other env vars
    happen to be set.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")

    weasel_providers.build_provider(_ns(model="llama3.2:3b"))

    assert len(capture_ollama["calls"]) == 1
    call = capture_ollama["calls"][0]
    assert call["model"] == "llama3.2:3b"
    # ``3b`` is not ``:cloud``, so context falls back to 131k.
    assert call["context_length"] == 131_072


def test_ollama_host_env_override_honored(
    clear_env: None,
    capture_ollama: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$OLLAMA_HOST`` overrides the default base URL."""
    monkeypatch.setenv("OLLAMA_API_KEY", "ol-xxx")
    monkeypatch.setenv("OLLAMA_HOST", "http://192.168.1.42:11434")

    weasel_providers.build_provider(_ns())

    assert capture_ollama["calls"][0]["base_url"] == "http://192.168.1.42:11434"


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


def test_no_keys_raises_friendly_error(
    clear_env: None,
) -> None:
    """No model + no env vars -> :class:`ValueError` listing every key."""
    with pytest.raises(ValueError) as exc_info:
        weasel_providers.build_provider(_ns())

    msg = str(exc_info.value)
    for needle in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "LLAMACPP_API_KEY",
        "OLLAMA_API_KEY",
        "WEASEL_MODEL",
    ):
        assert needle in msg, f"friendly error missing {needle!r}"


# ---------------------------------------------------------------------------
# Optional-flag plumbing
# ---------------------------------------------------------------------------


def test_max_tokens_forwarded(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``args.max_tokens`` is forwarded to the provider factory."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")

    weasel_providers.build_provider(_ns(max_tokens=4096))

    assert capture_factory["calls"][0].get("max_tokens") == 4096


def test_max_tokens_absent_is_not_forwarded(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``max_tokens`` is missing on the namespace it must not be passed."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")

    weasel_providers.build_provider(_ns())

    assert "max_tokens" not in capture_factory["calls"][0]


def test_max_tokens_forwarded_to_ollama(
    clear_env: None,
    capture_ollama: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Ollama path also honors ``args.max_tokens``."""
    monkeypatch.setenv("OLLAMA_API_KEY", "ol-xxx")

    weasel_providers.build_provider(_ns(max_tokens=2048))

    assert capture_ollama["calls"][0].get("max_tokens") == 2048


def test_no_color_attribute_tolerated(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``args.no_color`` is read without raising, even when ``True``."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")

    weasel_providers.build_provider(_ns(no_color=True))

    assert capture_factory["calls"][0]["model"] == "gpt-4o"


def test_namespace_without_no_color_attribute(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A namespace missing ``no_color`` is fine (getattr default)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")
    ns = argparse.Namespace(model=None)

    weasel_providers.build_provider(ns)

    assert capture_factory["calls"][0]["model"] == "gpt-4o"


# ---------------------------------------------------------------------------
# Catalog formatting
# ---------------------------------------------------------------------------


def test_resolved_catalog_shape() -> None:
    """The catalog enumerates every chain step in order."""
    catalog = weasel_providers.resolved_catalog()
    sources = [src for _model, src in catalog]
    # The list is order-significant: it mirrors the resolution chain.
    assert sources[0] == "OPENAI_API_KEY"
    assert sources[1] == "ANTHROPIC_API_KEY"
    assert sources[2] == "OPENROUTER_API_KEY"
    assert "LLAMACPP_API_KEY" in sources[3]
    assert "OLLAMA_API_KEY" in sources[4]
    assert len(catalog) == 5


def test_format_catalog_contains_every_default() -> None:
    """The textual format names every default model id."""
    text = weasel_providers.format_catalog()
    for needle in (
        "gpt-4o",
        "claude-sonnet-4-6",
        "openai/gpt-4o",
        "gpt-oss",
        "qwen3.5:cloud",
        "127.0.0.1:8888",
        "127.0.0.1:11434",
    ):
        assert needle in text, f"catalog missing {needle!r}"


# ---------------------------------------------------------------------------
# Returned provider type sanity check
# ---------------------------------------------------------------------------


def test_returned_object_is_factory_output(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``build_provider`` returns whatever the factory returns."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")

    result = weasel_providers.build_provider(_ns())

    assert isinstance(result, _StubProvider)
    assert result.model_name == "gpt-4o"
