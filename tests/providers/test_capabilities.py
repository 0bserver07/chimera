"""Tests for the declarative provider capability matrix.

Covers:

* protocol defaults, provider overrides, per-model (prefix) overrides, and the
  resolution / merge order;
* the :class:`CompatFlags` projection and ``register_capabilities`` validation;
* the fictional ``acme`` provider — the ~20-line data-row acceptance bar;
* a no-behavior-change pin: the matrix reproduces the *exact* pre-refactor
  quirk logic for OpenAI-compat flag detection and Anthropic-compat output
  caps, and the protocol defaults match a frozen snapshot.
"""
from __future__ import annotations

import copy
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import chimera.providers.capabilities as cap
from chimera.providers.capabilities import (
    PROTOCOL_DEFAULTS,
    CacheStyle,
    CompatFlags,
    ProviderCapabilities,
    ThinkingFormat,
    WireProtocol,
    register_capabilities,
    resolve_capabilities,
)
from chimera.types import Message


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_registry() -> Any:
    """Snapshot and restore the override registries around a test.

    ``register_capabilities`` mutates module-global tables; tests that register
    overrides must not leak into the snapshot pins (or each other).
    """
    prov = copy.deepcopy(cap._PROVIDER_OVERRIDES)
    mod = copy.deepcopy(cap._MODEL_OVERRIDES)
    try:
        yield
    finally:
        cap._PROVIDER_OVERRIDES.clear()
        cap._PROVIDER_OVERRIDES.update(prov)
        for proto in list(cap._MODEL_OVERRIDES):
            cap._MODEL_OVERRIDES[proto].clear()
            cap._MODEL_OVERRIDES[proto].update(mod.get(proto, {}))


def _stub_response(payload: dict[str, Any] | None = None) -> MagicMock:
    """A MagicMock shaped like a successful OpenAI-compat 200 response."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload or {
        "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    return resp


# ---------------------------------------------------------------------------
# Protocol defaults + resolution
# ---------------------------------------------------------------------------


def test_protocol_defaults_cover_every_wire_protocol() -> None:
    assert set(PROTOCOL_DEFAULTS) == set(WireProtocol)
    for protocol, caps in PROTOCOL_DEFAULTS.items():
        assert caps.protocol is protocol


def test_resolve_with_no_overrides_returns_protocol_default() -> None:
    for protocol in WireProtocol:
        assert resolve_capabilities(protocol) == PROTOCOL_DEFAULTS[protocol]


def test_openai_compat_reasoning_model_override() -> None:
    """o1/o3/o4/gpt-5 flip the max-tokens field, drop temperature, mark effort."""
    for model in ("o1-preview", "o3-mini", "o4-mini", "gpt-5-turbo"):
        caps = resolve_capabilities(WireProtocol.OPENAI_COMPAT, model=model)
        assert caps.max_tokens_field == "max_completion_tokens"
        assert caps.supports_temperature is False
        assert caps.thinking_format is ThinkingFormat.OPENAI_EFFORT
    # A non-reasoning id keeps the permissive default.
    default = resolve_capabilities(WireProtocol.OPENAI_COMPAT, model="glm-5")
    assert default == PROTOCOL_DEFAULTS[WireProtocol.OPENAI_COMPAT]


def test_anthropic_compat_large_output_override() -> None:
    for model in ("glm-5.2", "kimi-k2", "qwen3-coder", "deepseek-v4", "z-1"):
        caps = resolve_capabilities(WireProtocol.ANTHROPIC_COMPAT, model=model)
        assert caps.default_max_tokens == 32_768
    claude = resolve_capabilities(WireProtocol.ANTHROPIC_COMPAT, model="claude-sonnet-4")
    assert claude.default_max_tokens == 8_192


def test_model_override_is_case_insensitive() -> None:
    upper = resolve_capabilities(WireProtocol.OPENAI_COMPAT, model="O3-MINI")
    assert upper.max_tokens_field == "max_completion_tokens"
    upper_glm = resolve_capabilities(WireProtocol.ANTHROPIC_COMPAT, model="GLM-5.2")
    assert upper_glm.default_max_tokens == 32_768


# ---------------------------------------------------------------------------
# Provider + model override layering
# ---------------------------------------------------------------------------


def test_provider_override_applies(isolated_registry: None) -> None:
    register_capabilities(
        WireProtocol.OPENAI_COMPAT,
        provider="testco",
        supports_strict_tools=True,
        extra_payload={"house": 1},
    )
    caps = resolve_capabilities(WireProtocol.OPENAI_COMPAT, provider="testco")
    assert caps.supports_strict_tools is True
    assert caps.extra_payload == {"house": 1}
    # Absent the provider hint, the default is untouched.
    assert resolve_capabilities(WireProtocol.OPENAI_COMPAT).supports_strict_tools is False


def test_extra_payload_merges_additively_across_layers(isolated_registry: None) -> None:
    register_capabilities(
        WireProtocol.OPENAI_COMPAT, provider="testco", extra_payload={"a": 1},
    )
    register_capabilities(
        WireProtocol.OPENAI_COMPAT, model_prefix="zz-", extra_payload={"b": 2},
    )
    caps = resolve_capabilities(
        WireProtocol.OPENAI_COMPAT, provider="testco", model="zz-1",
    )
    assert caps.extra_payload == {"a": 1, "b": 2}


def test_model_layer_wins_over_provider_layer(isolated_registry: None) -> None:
    register_capabilities(
        WireProtocol.OPENAI_COMPAT, provider="testco", supports_temperature=False,
    )
    register_capabilities(
        WireProtocol.OPENAI_COMPAT, model_prefix="yy-", supports_temperature=True,
    )
    caps = resolve_capabilities(
        WireProtocol.OPENAI_COMPAT, provider="testco", model="yy-9",
    )
    assert caps.supports_temperature is True  # most-specific (model) wins


def test_longest_model_prefix_wins(isolated_registry: None) -> None:
    register_capabilities(
        WireProtocol.OPENAI_COMPAT, model_prefix="ab", extra_payload={"who": "short"},
    )
    register_capabilities(
        WireProtocol.OPENAI_COMPAT, model_prefix="abcd", extra_payload={"who": "long"},
    )
    caps = resolve_capabilities(WireProtocol.OPENAI_COMPAT, model="abcde")
    assert caps.extra_payload == {"who": "long"}


# ---------------------------------------------------------------------------
# register_capabilities validation
# ---------------------------------------------------------------------------


def test_register_requires_exactly_one_selector() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        register_capabilities(WireProtocol.OPENAI_COMPAT)
    with pytest.raises(ValueError, match="exactly one"):
        register_capabilities(
            WireProtocol.OPENAI_COMPAT, provider="x", model_prefix="y",
        )


def test_register_rejects_unknown_field(isolated_registry: None) -> None:
    with pytest.raises(ValueError, match="unknown ProviderCapabilities field"):
        register_capabilities(
            WireProtocol.OPENAI_COMPAT, provider="x", not_a_field=True,
        )


# ---------------------------------------------------------------------------
# CompatFlags projection
# ---------------------------------------------------------------------------


def test_to_compat_flags_projection() -> None:
    assert (
        resolve_capabilities(WireProtocol.OPENAI_COMPAT).to_compat_flags()
        == CompatFlags()
    )
    assert resolve_capabilities(
        WireProtocol.OPENAI_COMPAT, model="o3-mini",
    ).to_compat_flags() == CompatFlags(
        max_tokens_field="max_completion_tokens", supports_temperature=False,
    )


def test_to_compat_flags_copies_extra_payload(isolated_registry: None) -> None:
    register_capabilities(
        WireProtocol.OPENAI_COMPAT, provider="testco", extra_payload={"a": 1},
    )
    caps = resolve_capabilities(WireProtocol.OPENAI_COMPAT, provider="testco")
    flags = caps.to_compat_flags()
    flags.extra_payload["mutated"] = True
    # Mutating the projection must not touch the shared matrix record.
    assert "mutated" not in caps.extra_payload


# ---------------------------------------------------------------------------
# The fictional ``acmecloud`` provider — the ~20-line acceptance bar
# ---------------------------------------------------------------------------


class TestAcmeCloudBar:
    """A brand-new openai-compat backend added as pure data resolves + posts.

    ``acmecloud`` ships as ``chimera/providers/acmecloud.py``: a base URL, a
    capability row, and a registry lambda — no new ``Provider`` subclass.
    """

    def test_acmecloud_is_registered(self) -> None:
        from chimera.providers.registry import (
            _ensure_builtins_registered,
            get_provider_factory,
            list_providers,
        )

        _ensure_builtins_registered()
        assert "acmecloud" in list_providers()
        assert get_provider_factory("acmecloud") is not None

    def test_acmecloud_resolves_with_its_capabilities(self) -> None:
        pytest.importorskip("httpx")
        from chimera.providers.factory import create_provider

        with patch("chimera.providers.compatible.httpx") as mock_httpx:
            mock_httpx.post.return_value = _stub_response()
            provider = create_provider(
                provider_type="acmecloud", model="acmecloud-fast", api_key="k",
            )

        assert provider.model_name == "acmecloud-fast"
        assert provider._base_url == "https://api.acmecloud.example/v1"  # noqa: SLF001
        caps = provider._capabilities  # noqa: SLF001
        assert caps.protocol is WireProtocol.OPENAI_COMPAT
        assert caps.supports_strict_tools is True
        assert caps.extra_payload == {"acmecloud_reasoning": "auto"}

    def test_acmecloud_posts_to_the_right_endpoint_shape(self) -> None:
        pytest.importorskip("httpx")
        from chimera.providers.factory import create_provider

        with patch("chimera.providers.compatible.httpx") as mock_httpx:
            mock_httpx.post.return_value = _stub_response()
            provider = create_provider(
                provider_type="acmecloud", model="acmecloud-fast", api_key="k",
            )
            provider.complete(
                [Message.user("hi")],
                tools=[{
                    "name": "t",
                    "description": "d",
                    "input_schema": {"type": "object", "properties": {}},
                }],
                max_tokens=5,
            )

        args, kwargs = mock_httpx.post.call_args
        assert args[0] == "https://api.acmecloud.example/v1/chat/completions"
        payload = kwargs["json"]
        # House reasoning knob (extra_payload) rode along, as data.
        assert payload["acmecloud_reasoning"] == "auto"
        # supports_strict_tools drove the tool wire-shape.
        assert payload["tools"][0]["function"]["strict"] is True


# ---------------------------------------------------------------------------
# No-behavior-change pins (snapshots of the pre-refactor quirk logic)
# ---------------------------------------------------------------------------


_LEGACY_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5")


def _legacy_detect_compat_flags(model: str) -> CompatFlags:
    """The pre-refactor ``detect_compat_flags`` body, verbatim."""
    bare = model.lower().split("/")[-1]
    if bare.startswith(_LEGACY_REASONING_PREFIXES):
        return CompatFlags(
            max_tokens_field="max_completion_tokens", supports_temperature=False,
        )
    return CompatFlags()


@pytest.mark.parametrize(
    "model",
    [
        "glm-5", "glm-5.2", "o1", "o1-preview", "o3", "o3-mini", "o4-mini",
        "gpt-5", "gpt-5-turbo", "gpt-4o", "gpt-4o-mini", "openai/o3-mini",
        "anthropic/claude-sonnet-4", "deepseek-r1", "grok-3", "kimi-k2",
        "qwen3-coder", "mistral-large", "Together/o3-MINI",
    ],
)
def test_openai_compat_projection_matches_legacy(model: str) -> None:
    """The matrix-backed detection is byte-identical to the old prefix tuple."""
    from chimera.providers.compatible import detect_compat_flags

    assert detect_compat_flags(model) == _legacy_detect_compat_flags(model)


def _legacy_anthropic_default_max_tokens(model: str) -> int:
    """The pre-refactor ``AnthropicProvider._default_max_tokens`` body, verbatim."""
    lowered = model.lower()
    if lowered.startswith(("glm", "kimi", "qwen", "deepseek", "z-")):
        return 32_768
    return 8_192


@pytest.mark.parametrize(
    "model",
    [
        "glm-5.2", "glm-4.6", "kimi-k2-0905-preview", "qwen3-coder-30b",
        "deepseek-v4-pro", "z-model", "claude-opus-4", "claude-sonnet-4-5",
        "claude-haiku-3.5", "GLM-5.2", "Kimi-K2", "gpt-4o",
    ],
)
def test_anthropic_default_max_tokens_matches_legacy(model: str) -> None:
    caps = resolve_capabilities(WireProtocol.ANTHROPIC_COMPAT, model=model)
    assert caps.default_max_tokens == _legacy_anthropic_default_max_tokens(model)


def test_protocol_defaults_snapshot() -> None:
    """Frozen pin: the protocol baselines must not drift silently.

    If this fails, the matrix defaults changed — update the expected records
    intentionally (and check downstream request-shaping did not regress).
    """
    expected = {
        WireProtocol.OPENAI_COMPAT: ProviderCapabilities(
            protocol=WireProtocol.OPENAI_COMPAT,
            max_tokens_field="max_tokens",
            supports_temperature=True,
            thinking_format=ThinkingFormat.NONE,
            cache_style=CacheStyle.OPENAI_AUTOMATIC,
            supports_strict_tools=False,
            accepts_extra_headers=True,
            supports_stop_sequences=True,
            tiered_pricing=False,
            default_max_tokens=8_192,
        ),
        WireProtocol.ANTHROPIC_COMPAT: ProviderCapabilities(
            protocol=WireProtocol.ANTHROPIC_COMPAT,
            max_tokens_field="max_tokens",
            supports_temperature=True,
            thinking_format=ThinkingFormat.ANTHROPIC_BUDGET,
            cache_style=CacheStyle.ANTHROPIC_EPHEMERAL,
            one_hour_cache_write_premium=True,
            supports_strict_tools=False,
            accepts_extra_headers=True,
            supports_stop_sequences=True,
            tiered_pricing=True,
            default_max_tokens=8_192,
        ),
        WireProtocol.GOOGLE: ProviderCapabilities(
            protocol=WireProtocol.GOOGLE,
            max_tokens_field="max_output_tokens",
            supports_temperature=True,
            thinking_format=ThinkingFormat.NONE,
            cache_style=CacheStyle.NONE,
            supports_strict_tools=False,
            accepts_extra_headers=False,
            supports_stop_sequences=True,
            tiered_pricing=True,
            default_max_tokens=8_192,
        ),
    }
    assert PROTOCOL_DEFAULTS == expected


# ---------------------------------------------------------------------------
# Real registered providers expose matrix-sourced capabilities
# ---------------------------------------------------------------------------


def test_compatible_provider_exposes_capabilities() -> None:
    pytest.importorskip("httpx")
    from chimera.providers.compatible import OpenAICompatibleProvider

    reasoning = OpenAICompatibleProvider(
        model="o3-mini", base_url="https://x/v1", api_key="k",
    )
    assert reasoning._capabilities.protocol is WireProtocol.OPENAI_COMPAT  # noqa: SLF001
    assert reasoning._flags.max_tokens_field == "max_completion_tokens"  # noqa: SLF001

    plain = OpenAICompatibleProvider(
        model="deepseek-chat", base_url="https://x/v1", api_key="k",
    )
    assert plain._flags.max_tokens_field == "max_tokens"  # noqa: SLF001


def test_anthropic_provider_exposes_capabilities() -> None:
    pytest.importorskip("anthropic")
    from chimera.providers.anthropic import AnthropicProvider

    glm = AnthropicProvider(model="glm-5.2", api_key="x")
    assert glm._capabilities.protocol is WireProtocol.ANTHROPIC_COMPAT  # noqa: SLF001
    assert glm._default_max_tokens == 32_768  # noqa: SLF001

    claude = AnthropicProvider(model="claude-sonnet-4", api_key="x")
    assert claude._default_max_tokens == 8_192  # noqa: SLF001
