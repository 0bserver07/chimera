"""Tests for ``chimera.otter.providers`` (agent O12).

Covers the env-var-controlled provider chain:

* Explicit ``args.model`` wins over everything.
* ``$OTTER_MODEL`` wins over default-by-env-var.
* ``$ANTHROPIC_API_KEY`` -> Anthropic default.
* ``$OPENROUTER_API_KEY`` -> OpenRouter default (routed via ``compatible``
  provider against ``openrouter.ai`` when the model id contains a ``/``).
* ``$OPENAI_API_KEY`` -> OpenAI default.
* No keys + no model -> friendly :class:`ValueError`.
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

from chimera.otter import providers as otter_providers


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _StubProvider:
    """Minimal stand-in for :class:`chimera.providers.base.Provider`."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.model_name = kwargs.get("model", "")


def _ns(**overrides: Any) -> argparse.Namespace:
    """Build a default-ish otter argparse namespace for tests.

    Keeping the helper centralized lets us update the otter flag surface
    in one place if it grows; today we only need ``model`` defaulting to
    ``None`` and ``max_tokens`` defaulting to absent.
    """
    base: dict[str, Any] = {"model": None, "no_color": False}
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture
def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip all otter-relevant env vars so tests start from a clean slate."""
    for var in (
        "OTTER_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
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


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------


def test_explicit_model_wins(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``args.model`` overrides every env var."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    monkeypatch.setenv("OTTER_MODEL", "claude-haiku-4")

    otter_providers.build_provider(_ns(model="gpt-4o"))

    assert capture_factory["calls"][0]["model"] == "gpt-4o"


def test_otter_model_env_wins_over_default(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$OTTER_MODEL`` wins over the env-var-derived default."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    monkeypatch.setenv("OTTER_MODEL", "claude-opus-4-1")

    otter_providers.build_provider(_ns())

    assert capture_factory["calls"][0]["model"] == "claude-opus-4-1"


def test_default_anthropic_when_anthropic_key_set(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When only ``$ANTHROPIC_API_KEY`` is set, default to the anthropic model."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")

    otter_providers.build_provider(_ns())

    assert capture_factory["calls"][0]["model"] == "claude-sonnet-4-6"


def test_default_openrouter_when_only_openrouter_key_set(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When only ``$OPENROUTER_API_KEY`` is set, route through ``compatible``."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xxx")

    otter_providers.build_provider(_ns())

    call = capture_factory["calls"][0]
    assert call["provider_type"] == "compatible"
    assert call["model"] == "anthropic/claude-sonnet-4"
    assert call["api_key"] == "sk-or-xxx"
    assert call["base_url"] == "https://openrouter.ai/api/v1"


def test_default_openai_when_only_openai_key_set(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When only ``$OPENAI_API_KEY`` is set, default to ``gpt-4o``."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")

    otter_providers.build_provider(_ns())

    assert capture_factory["calls"][0]["model"] == "gpt-4o"
    # Bare OpenAI must NOT route through openrouter.
    assert "provider_type" not in capture_factory["calls"][0]


def test_anthropic_takes_priority_over_openai(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both Anthropic and OpenAI keys are set, default to Anthropic.

    Documents the resolution order: Anthropic > OpenRouter > OpenAI.
    Users who want OpenAI explicitly should pass ``--model gpt-4o`` or
    set ``$OTTER_MODEL=gpt-4o``.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")

    otter_providers.build_provider(_ns())

    assert capture_factory["calls"][0]["model"] == "claude-sonnet-4-6"


def test_openrouter_routes_when_model_has_slash(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenRouter routing kicks in when ``$OPENROUTER_API_KEY`` is set
    AND the resolved model id contains a ``/`` (vendor/name pattern)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xxx")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")

    otter_providers.build_provider(_ns(model="meta-llama/llama-3.3-70b"))

    call = capture_factory["calls"][0]
    assert call["provider_type"] == "compatible"
    assert call["model"] == "meta-llama/llama-3.3-70b"
    assert call["base_url"] == "https://openrouter.ai/api/v1"


def test_openrouter_skipped_when_model_lacks_slash(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$OPENROUTER_API_KEY`` alone doesn't hijack a bare model id.

    A bare ``claude-sonnet-4-6`` should fall through to the regular
    factory inference even when the user has an OpenRouter key set
    (because they may simply have it exported in their shell rc).
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xxx")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")

    otter_providers.build_provider(_ns(model="claude-sonnet-4-6"))

    call = capture_factory["calls"][0]
    assert call.get("provider_type") is None or call.get("provider_type") != "compatible"
    assert call["model"] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


def test_no_keys_raises_friendly_error(
    clear_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No model + no env vars -> :class:`ValueError` with all three keys named."""
    # We deliberately do NOT install the capture_factory fixture here so
    # the real factory would be exercised on failure. The error must fire
    # before that.
    with pytest.raises(ValueError) as exc_info:
        otter_providers.build_provider(_ns())

    msg = str(exc_info.value)
    assert "ANTHROPIC_API_KEY" in msg
    assert "OPENAI_API_KEY" in msg
    assert "OPENROUTER_API_KEY" in msg
    assert "OTTER_MODEL" in msg


# ---------------------------------------------------------------------------
# Optional-flag plumbing
# ---------------------------------------------------------------------------


def test_max_tokens_forwarded(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``args.max_tokens`` is forwarded to the provider factory."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")

    otter_providers.build_provider(_ns(max_tokens=4096))

    assert capture_factory["calls"][0].get("max_tokens") == 4096


def test_max_tokens_absent_is_not_forwarded(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``max_tokens`` is not on the namespace it must not be passed."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")

    otter_providers.build_provider(_ns())

    assert "max_tokens" not in capture_factory["calls"][0]


def test_no_color_attribute_tolerated(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``args.no_color`` is read without raising, even when ``True``."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")

    otter_providers.build_provider(_ns(no_color=True))

    assert capture_factory["calls"][0]["model"] == "claude-sonnet-4-6"


def test_namespace_without_no_color_attribute(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling on a namespace missing ``no_color`` is fine (getattr default)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    ns = argparse.Namespace(model=None)

    otter_providers.build_provider(ns)

    assert capture_factory["calls"][0]["model"] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Returned provider type sanity check
# ---------------------------------------------------------------------------


def test_returned_object_is_factory_output(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``build_provider`` returns whatever the factory returns."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")

    result = otter_providers.build_provider(_ns())

    assert isinstance(result, _StubProvider)
    assert result.model_name == "claude-sonnet-4-6"
