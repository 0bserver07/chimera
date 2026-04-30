"""Tests for ``chimera.ferret.providers`` (agent FF6).

Covers the env-var-controlled provider chain:

* Explicit ``args.model`` wins over everything.
* ``$FERRET_MODEL`` wins over default-by-env-var.
* ``$OPENAI_API_KEY`` -> OpenAI default (the OpenAI-flagship distinguishing
  posture vs otter's Anthropic-first ordering).
* ``$ANTHROPIC_API_KEY`` -> Anthropic default.
* ``$OPENROUTER_API_KEY`` -> OpenRouter default (routed via ``compatible``
  provider against ``openrouter.ai`` when the model id contains a ``/``).
* No keys + no model -> friendly :class:`ValueError`.
* Ollama-tag detection routes through :class:`OllamaProvider`.
* ``args.max_tokens`` forwards to the factory.
* ``args.no_color`` is read without raising even when other flags are
  absent.

Tests stub :func:`chimera.providers.factory.create_provider` so we never
hit real SDKs / network. Each test isolates env vars via ``monkeypatch``.
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from chimera.ferret import providers as ferret_providers


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _StubProvider:
    """Minimal stand-in for :class:`chimera.providers.base.Provider`."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.model_name = kwargs.get("model", "")


def _ns(**overrides: Any) -> argparse.Namespace:
    """Build a default-ish ferret argparse namespace for tests.

    Keeping the helper centralized lets us update the ferret flag surface
    in one place if it grows; today we only need ``model`` defaulting to
    ``None`` and ``max_tokens`` defaulting to absent.
    """
    base: dict[str, Any] = {"model": None, "no_color": False}
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture
def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip all ferret-relevant env vars so tests start from a clean slate."""
    for var in (
        "FERRET_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "OLLAMA_HOST",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def capture_factory(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace :func:`create_provider` with a capturing stub.

    Returns the call-record dict so tests can assert on the call shape.
    """
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
    """Replace :class:`OllamaProvider` with a capturing stub.

    Returns the call-record dict so tests can assert on construction args
    without importing the real Ollama provider (and its httpx dependency).
    """
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
# Model resolution
# ---------------------------------------------------------------------------


def test_explicit_model_wins(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``args.model`` overrides every env var."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")
    monkeypatch.setenv("FERRET_MODEL", "claude-haiku-4")

    ferret_providers.build_provider(_ns(model="gpt-4o-mini"))

    assert capture_factory["calls"][0]["model"] == "gpt-4o-mini"


def test_ferret_model_env_wins_over_default(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$FERRET_MODEL`` wins over the env-var-derived default."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")
    monkeypatch.setenv("FERRET_MODEL", "gpt-4o-mini")

    ferret_providers.build_provider(_ns())

    assert capture_factory["calls"][0]["model"] == "gpt-4o-mini"


def test_default_openai_when_only_openai_key_set(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When only ``$OPENAI_API_KEY`` is set, default to ``gpt-4o``."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")

    ferret_providers.build_provider(_ns())

    assert capture_factory["calls"][0]["model"] == "gpt-4o"
    # Bare OpenAI must NOT route through openrouter.
    assert "provider_type" not in capture_factory["calls"][0]


def test_default_anthropic_when_only_anthropic_key_set(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When only ``$ANTHROPIC_API_KEY`` is set, default to the Anthropic model."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")

    ferret_providers.build_provider(_ns())

    assert capture_factory["calls"][0]["model"] == "claude-sonnet-4-6"


def test_default_openrouter_when_only_openrouter_key_set(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When only ``$OPENROUTER_API_KEY`` is set, route through ``compatible``."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xxx")

    ferret_providers.build_provider(_ns())

    call = capture_factory["calls"][0]
    assert call["provider_type"] == "compatible"
    assert call["model"] == "openai/gpt-4o"
    assert call["api_key"] == "sk-or-xxx"
    assert call["base_url"] == "https://openrouter.ai/api/v1"


def test_openai_takes_priority_over_anthropic(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both OpenAI and Anthropic keys are set, default to OpenAI.

    Documents the ferret resolution order: OpenAI > Anthropic > OpenRouter.
    This is the load-bearing distinction from otter's Anthropic-first
    chain. Users who want Anthropic explicitly should pass
    ``--model claude-sonnet-4-6`` or set ``$FERRET_MODEL=claude-sonnet-4-6``.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")

    ferret_providers.build_provider(_ns())

    assert capture_factory["calls"][0]["model"] == "gpt-4o"


def test_openai_takes_priority_over_openrouter(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI direct beats OpenRouter when both keys are set."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xxx")

    ferret_providers.build_provider(_ns())

    call = capture_factory["calls"][0]
    # gpt-4o has no slash, so the openrouter heuristic should NOT fire.
    assert call["model"] == "gpt-4o"
    assert "provider_type" not in call


def test_anthropic_takes_priority_over_openrouter(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Anthropic and OpenRouter keys are both set (no OpenAI),
    Anthropic wins."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xxx")

    ferret_providers.build_provider(_ns())

    call = capture_factory["calls"][0]
    # claude-sonnet-4-6 has no slash, so openrouter heuristic must NOT fire.
    assert call["model"] == "claude-sonnet-4-6"
    assert "provider_type" not in call


def test_openrouter_routes_when_model_has_slash(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenRouter routing kicks in when ``$OPENROUTER_API_KEY`` is set
    AND the resolved model id contains a ``/`` (vendor/name pattern)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xxx")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")

    ferret_providers.build_provider(_ns(model="meta-llama/llama-3.3-70b"))

    call = capture_factory["calls"][0]
    assert call["provider_type"] == "compatible"
    assert call["model"] == "meta-llama/llama-3.3-70b"
    assert call["base_url"] == "https://openrouter.ai/api/v1"
    assert call["api_key"] == "sk-or-xxx"


def test_openrouter_skipped_when_model_lacks_slash(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$OPENROUTER_API_KEY`` alone doesn't hijack a bare model id.

    A bare ``gpt-4o`` should fall through to the regular factory inference
    even when the user has an OpenRouter key set (because they may simply
    have it exported in their shell rc).
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xxx")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")

    ferret_providers.build_provider(_ns(model="gpt-4o"))

    call = capture_factory["calls"][0]
    assert call.get("provider_type") is None or call.get("provider_type") != "compatible"
    assert call["model"] == "gpt-4o"


# ---------------------------------------------------------------------------
# Ollama-tag detection
# ---------------------------------------------------------------------------


def test_ollama_cloud_tag_routes_to_ollama_provider(
    clear_env: None,
    capture_ollama: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``name:cloud`` model id routes to OllamaProvider with 262k context."""
    # No real API key needed — the explicit model bypasses env resolution.
    ferret_providers.build_provider(_ns(model="glm-5.1:cloud"))

    assert len(capture_ollama["calls"]) == 1
    call = capture_ollama["calls"][0]
    assert call["model"] == "glm-5.1:cloud"
    assert call["base_url"] == "http://localhost:11434"
    assert call["context_length"] == 262_144


def test_ollama_local_tag_routes_to_ollama_provider(
    clear_env: None,
    capture_ollama: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``name:tag`` (non-cloud) model id routes with 131k context."""
    ferret_providers.build_provider(_ns(model="llama3.2:3b"))

    assert len(capture_ollama["calls"]) == 1
    call = capture_ollama["calls"][0]
    assert call["model"] == "llama3.2:3b"
    assert call["context_length"] == 131_072


def test_ollama_honors_ollama_host_env(
    clear_env: None,
    capture_ollama: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$OLLAMA_HOST`` overrides the default base URL."""
    monkeypatch.setenv("OLLAMA_HOST", "http://10.0.0.5:11434")

    ferret_providers.build_provider(_ns(model="llama3.2:3b"))

    assert capture_ollama["calls"][0]["base_url"] == "http://10.0.0.5:11434"


def test_ollama_forwards_max_tokens(
    clear_env: None,
    capture_ollama: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``args.max_tokens`` reaches the OllamaProvider constructor."""
    ferret_providers.build_provider(_ns(model="llama3.2:3b", max_tokens=2048))

    assert capture_ollama["calls"][0]["max_tokens"] == 2048


def test_ollama_skipped_for_slash_ids(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``vendor/name`` id with no OpenRouter key falls through to factory.

    The Ollama heuristic explicitly skips slash-shaped ids so OpenRouter
    convention doesn't get hijacked.
    """
    # No OPENROUTER_API_KEY — the slash id should drop into the factory
    # rather than the Ollama or OpenRouter paths.
    ferret_providers.build_provider(_ns(model="vendor/somemodel"))

    assert capture_factory["calls"][0]["model"] == "vendor/somemodel"
    # Bare factory call (no openrouter routing).
    assert "provider_type" not in capture_factory["calls"][0]


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


def test_no_keys_raises_friendly_error(
    clear_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No model + no env vars -> :class:`ValueError` listing every key."""
    # We deliberately do NOT install the capture_factory fixture here so
    # the real factory would be exercised on failure. The error must fire
    # before that.
    with pytest.raises(ValueError) as exc_info:
        ferret_providers.build_provider(_ns())

    msg = str(exc_info.value)
    assert "OPENAI_API_KEY" in msg
    assert "ANTHROPIC_API_KEY" in msg
    assert "OPENROUTER_API_KEY" in msg
    assert "FERRET_MODEL" in msg
    # Ferret's brand prefix should appear so users know which CLI raised.
    assert "ferret" in msg


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

    ferret_providers.build_provider(_ns(max_tokens=4096))

    assert capture_factory["calls"][0].get("max_tokens") == 4096


def test_max_tokens_absent_is_not_forwarded(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``max_tokens`` is not on the namespace it must not be passed."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")

    ferret_providers.build_provider(_ns())

    assert "max_tokens" not in capture_factory["calls"][0]


def test_no_color_attribute_tolerated(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``args.no_color`` is read without raising, even when ``True``."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")

    ferret_providers.build_provider(_ns(no_color=True))

    assert capture_factory["calls"][0]["model"] == "gpt-4o"


def test_namespace_without_no_color_attribute(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling on a namespace missing ``no_color`` is fine (getattr default)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")
    ns = argparse.Namespace(model=None)

    ferret_providers.build_provider(ns)

    assert capture_factory["calls"][0]["model"] == "gpt-4o"


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

    result = ferret_providers.build_provider(_ns())

    assert isinstance(result, _StubProvider)
    assert result.model_name == "gpt-4o"


# ---------------------------------------------------------------------------
# Module-level constants — locked-in defaults for downstream consumers
# ---------------------------------------------------------------------------


def test_default_openai_model_constant() -> None:
    """Locks the OpenAI default so docs / SPEC.md stay in sync."""
    assert ferret_providers._DEFAULT_OPENAI_MODEL == "gpt-4o"


def test_default_anthropic_model_constant() -> None:
    """Locks the Anthropic default."""
    assert ferret_providers._DEFAULT_ANTHROPIC_MODEL == "claude-sonnet-4-6"


def test_default_openrouter_model_constant() -> None:
    """Locks the OpenRouter default to the OpenAI flagship route."""
    assert ferret_providers._DEFAULT_OPENROUTER_MODEL == "openai/gpt-4o"
