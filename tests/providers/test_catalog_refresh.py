"""Catalog refresh — wave 13, task W13-E2.

Adds explicit catalog + pricing bindings for seven model families that
shipped between v0.4 and v0.5:

* ``qwen3-coder``, ``qwen3-coder-30b``, ``qwen3-32b`` (Alibaba, via Ollama)
* ``glm-4.6``, ``glm-5.1`` (Zhipu, Anthropic-compat via api.z.ai)
* ``deepseek-v3.1-terminus``, ``deepseek-coder-v3``
  (DeepSeek hosted OpenAI-compat API)
* ``gpt-oss-120b``, ``gpt-oss-20b`` (OpenAI open weights via Ollama)
* ``kimi-k2-0905-preview``, ``kimi-k2.5``
  (Moonshot Anthropic-compat at api.moonshot.ai)
* ``mistral-codestral-2511`` (Mistral coder via Ollama)
* ``gemma3-27b-instruct`` (Google open weights via Ollama)

Tests cover:

* The catalog ships explicit ``ModelConfig`` for every new id.
* ``_infer_provider`` resolves each id without raising and returns the
  documented provider type.
* ``calculate_cost`` resolves to the documented placeholder pricing
  (longest-prefix wins).
* Routing regressions — ``gpt-oss-*`` does NOT land on the OpenAI
  provider, ``gemma3-*`` does NOT fall through to the unknown-model
  error.

No live network calls — pure routing + pricing assertions.
"""

from __future__ import annotations

import pytest

from chimera.providers.catalog import ProviderCatalog
from chimera.providers.cost import PRICING, calculate_cost
from chimera.providers.factory import _infer_provider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_NEW_MODELS_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    "ollama": (
        "qwen3-coder",
        "qwen3-coder-30b",
        "qwen3-32b",
        "gpt-oss-120b",
        "gpt-oss-20b",
        "mistral-codestral-2511",
        "gemma3-27b-instruct",
    ),
    "compatible": (
        "deepseek-v3.1-terminus",
        "deepseek-coder-v3",
    ),
    "anthropic": (
        "glm-4.6",
        "glm-5.1",
        "kimi-k2-0905-preview",
        "kimi-k2.5",
    ),
}


def _all_new_models() -> list[str]:
    return [m for ids in _NEW_MODELS_BY_PROVIDER.values() for m in ids]


def _clear_inference_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip env vars that would otherwise sway ``_infer_provider``."""
    for var in (
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "MOONSHOT_API_KEY",
        "OLLAMA_HOST",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Catalog presence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", _all_new_models())
def test_catalog_has_entry(model: str) -> None:
    """Each new id ships an explicit ``ModelConfig`` in the default catalog."""
    catalog = ProviderCatalog.default()
    assert model in catalog.models, f"missing catalog entry: {model}"


@pytest.mark.parametrize("model", _all_new_models())
def test_catalog_entry_carries_cost_and_context(model: str) -> None:
    """Every new entry has a non-None cost tuple and a sane context window."""
    catalog = ProviderCatalog.default()
    cfg = catalog.get(model)
    assert cfg is not None
    assert cfg.cost is not None, f"{model}: missing cost tuple"
    assert cfg.context_window >= 64_000, (
        f"{model}: context_window={cfg.context_window} < 64k"
    )


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,expected_provider",
    [
        (m, prov)
        for prov, ids in _NEW_MODELS_BY_PROVIDER.items()
        for m in ids
    ],
)
def test_infer_provider_routing(
    model: str,
    expected_provider: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_infer_provider`` resolves every new id to the documented type."""
    _clear_inference_env(monkeypatch)
    assert _infer_provider(model) == expected_provider, (
        f"{model} -> {_infer_provider(model)}, expected {expected_provider}"
    )


@pytest.mark.parametrize("model", _all_new_models())
def test_infer_provider_does_not_raise(
    model: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No new id falls through to the unknown-model ``ValueError``."""
    _clear_inference_env(monkeypatch)
    # Should not raise.
    result = _infer_provider(model)
    assert result in {"anthropic", "openai", "google", "ollama", "compatible"}


def test_gpt_oss_not_routed_to_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: ``gpt-oss-*`` must NOT land on the OpenAI provider.

    OpenAI's hosted API does not serve the OSS weights — they live on
    Ollama. Without the ``gpt-oss`` prefix shortcut the generic ``gpt``
    branch would mis-route the call.
    """
    _clear_inference_env(monkeypatch)
    assert _infer_provider("gpt-oss-120b") == "ollama"
    assert _infer_provider("gpt-oss-20b") == "ollama"
    # Case-insensitive routing.
    assert _infer_provider("GPT-OSS-120B") == "ollama"
    # Plain ``gpt-4o`` still goes to OpenAI.
    assert _infer_provider("gpt-4o") == "openai"


def test_gemma_routes_to_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: bare ``gemma`` ids resolve via the Ollama prefix."""
    _clear_inference_env(monkeypatch)
    assert _infer_provider("gemma3-27b-instruct") == "ollama"
    assert _infer_provider("gemma2-9b") == "ollama"


def test_anthropic_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ANTHROPIC_BASE_URL`` still trumps the new prefixes (qwen / gemma).

    Mirrors the existing deepseek/qwen behaviour: a user who pointed
    ``$ANTHROPIC_BASE_URL`` at an Anthropic-compat relay wants the
    relay used regardless of the model id. Only ``gpt-oss-*`` sidesteps
    the override because the generic ``gpt`` exclusion list (which
    ``gpt-oss`` matches via prefix) keeps it off the anthropic path.
    """
    _clear_inference_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ollama")
    assert _infer_provider("qwen3-32b") == "anthropic"
    assert _infer_provider("gemma3-27b-instruct") == "anthropic"
    # gpt-oss-* should still route to Ollama prefix even with anthropic
    # env set, because the not-anthropic-prefix list catches "gpt".
    assert _infer_provider("gpt-oss-120b") == "ollama"


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", _all_new_models())
def test_pricing_table_contains_model(model: str) -> None:
    """Every new id has an entry in the static PRICING dict.

    The catalog also auto-registers via ``register_model_cost`` when
    ``ProviderCatalog.default()`` is instantiated, but we want the
    static table to carry these so cold callers (no catalog construction)
    still bill correctly.
    """
    # Force the catalog to build at least once (registers dynamic costs).
    ProviderCatalog.default()
    assert model in PRICING, f"PRICING missing: {model}"


def test_calculate_cost_glm_5_1_inherits_glm_5_pricing() -> None:
    """``glm-5.1`` placeholder bills at the glm-5 rate ($2 / $8 per Mtok)."""
    cost = calculate_cost(
        "glm-5.1",
        {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    # 2.0 + 8.0 = 10.0 USD per 1M+1M.
    assert cost == pytest.approx(10.0)


def test_calculate_cost_glm_4_6() -> None:
    """``glm-4.6`` placeholder bills at $0.6 / $2.2 per Mtok."""
    cost = calculate_cost(
        "glm-4.6",
        {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    # 0.6 + 2.2 = 2.8 USD per 1M+1M.
    assert cost == pytest.approx(2.8)


def test_calculate_cost_deepseek_coder_longest_prefix() -> None:
    """``deepseek-coder-v3`` matches its own entry, not ``deepseek-chat``.

    Both share the placeholder $0.27/$1.10 today, but pinning the
    longest-prefix behaviour now means a future split (e.g. coder at a
    different tier) won't silently fall back to ``deepseek-chat``.
    """
    assert PRICING["deepseek-coder-v3"] == (0.27, 1.10)
    assert PRICING["deepseek-v3.1-terminus"] == (0.27, 1.10)
    cost = calculate_cost(
        "deepseek-coder-v3",
        {"input_tokens": 1_000_000, "output_tokens": 0},
    )
    assert cost == pytest.approx(0.27)


def test_calculate_cost_local_models_zero() -> None:
    """Local-only ids (qwen / gpt-oss / gemma / mistral codestral) bill $0."""
    for model in (
        "qwen3-coder",
        "qwen3-coder-30b",
        "qwen3-32b",
        "gpt-oss-120b",
        "gpt-oss-20b",
        "mistral-codestral-2511",
        "gemma3-27b-instruct",
    ):
        cost = calculate_cost(
            model,
            {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
        )
        assert cost == pytest.approx(0.0), f"{model}: expected $0, got {cost}"


def test_calculate_cost_kimi_placeholder() -> None:
    """Kimi ids billed at the $0.6/$2.5 placeholder until Moonshot ships rates."""
    for model in ("kimi-k2-0905-preview", "kimi-k2.5"):
        cost = calculate_cost(
            model,
            {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
        )
        # 0.6 + 2.5 = 3.1 USD per 1M+1M.
        assert cost == pytest.approx(3.1), f"{model}: got {cost}"


# ---------------------------------------------------------------------------
# Catalog binding details
# ---------------------------------------------------------------------------


def test_catalog_glm_targets_z_ai_anthropic_compat() -> None:
    """GLM 4.6 / 5.1 entries point at api.z.ai's Anthropic-compat endpoint."""
    catalog = ProviderCatalog.default()
    for model in ("glm-4.6", "glm-5.1"):
        cfg = catalog.get(model)
        assert cfg is not None
        assert cfg.provider_type == "anthropic"
        assert cfg.base_url == "https://api.z.ai/api/anthropic"
        assert cfg.api_key_env == "ANTHROPIC_AUTH_TOKEN"


def test_catalog_kimi_targets_moonshot_anthropic_compat() -> None:
    """Kimi non-cloud ids point at api.moonshot.ai's Anthropic-compat endpoint."""
    catalog = ProviderCatalog.default()
    for model in ("kimi-k2-0905-preview", "kimi-k2.5"):
        cfg = catalog.get(model)
        assert cfg is not None
        assert cfg.provider_type == "anthropic"
        assert cfg.base_url == "https://api.moonshot.ai/anthropic"
        assert cfg.api_key_env == "MOONSHOT_API_KEY"


def test_catalog_deepseek_v3_targets_hosted_api() -> None:
    """DeepSeek V3 coder + V3.1 terminus point at api.deepseek.com/v1."""
    catalog = ProviderCatalog.default()
    for model in ("deepseek-coder-v3", "deepseek-v3.1-terminus"):
        cfg = catalog.get(model)
        assert cfg is not None
        assert cfg.provider_type == "compatible"
        assert cfg.base_url == "https://api.deepseek.com/v1"
        assert cfg.api_key_env == "DEEPSEEK_API_KEY"
        assert cfg.context_window == 128_000


def test_catalog_local_models_use_ollama_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local Ollama-backed entries resolve ``$OLLAMA_HOST`` for the base URL."""
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    catalog = ProviderCatalog.default()
    for model in (
        "qwen3-coder",
        "qwen3-coder-30b",
        "qwen3-32b",
        "gpt-oss-120b",
        "gpt-oss-20b",
        "mistral-codestral-2511",
        "gemma3-27b-instruct",
    ):
        cfg = catalog.get(model)
        assert cfg is not None
        assert cfg.provider_type == "ollama"
        assert cfg.resolve_base_url() == "http://localhost:11434"
