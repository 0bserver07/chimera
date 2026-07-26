"""Tests for DeepSeek-V4 provider integration (wave 12, task W12-1).

DeepSeek-V4 surfaces in two flavours:

* **Hosted API** (``deepseek-v4``, ``deepseek-v4-pro``) — DeepSeek's own
  OpenAI-compatible endpoint at ``https://api.deepseek.com/v1``. Routes
  through the ``compatible`` provider via the catalog binding.
* **Ollama cloud passthrough** (``deepseek-v4-pro:cloud``) — served by
  the local Ollama daemon proxying to ``ollama.com``. Routes through the
  ``ollama`` provider.

Tests cover:

* Prefix inference (``_infer_provider``) keeps the ``:cloud`` and bare
  variants on the right provider type.
* Catalog entries exist with the documented base URLs and pricing.
* ``calculate_cost`` resolves the new model ids to the placeholder
  pricing tuple (longest-prefix wins).
* Optional live smoke against a local Ollama daemon if both the daemon
  and the model tag are present.

No live network calls in the unit suite — the Ollama smoke is skipped
unless the daemon answers on ``localhost:11434`` AND the model tag is
listed.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request

import pytest

from chimera.providers.catalog import ProviderCatalog
from chimera.providers.cost import PRICING, calculate_cost
from chimera.providers.factory import _infer_provider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_inference_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip env vars that would otherwise sway ``_infer_provider``."""
    for var in (
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Factory inference
# ---------------------------------------------------------------------------


def test_infer_deepseek_v4_routes_to_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare ``deepseek-v4*`` ids land on the ``compatible`` provider."""
    _clear_inference_env(monkeypatch)
    assert _infer_provider("deepseek-v4") == "compatible"
    assert _infer_provider("deepseek-v4-pro") == "compatible"
    assert _infer_provider("DEEPSEEK-V4-PRO") == "compatible"


def test_infer_deepseek_cloud_routes_to_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``:cloud`` suffix routes to Ollama (cloud passthrough)."""
    _clear_inference_env(monkeypatch)
    assert _infer_provider("deepseek-v4-pro:cloud") == "ollama"
    assert _infer_provider("deepseek-v4:cloud") == "ollama"


def test_infer_deepseek_legacy_ids_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing ``deepseek-chat`` / ``deepseek-reasoner`` still infer."""
    _clear_inference_env(monkeypatch)
    assert _infer_provider("deepseek-chat") == "compatible"
    assert _infer_provider("deepseek-reasoner") == "compatible"


def test_infer_deepseek_with_anthropic_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ANTHROPIC_BASE_URL`` wins over the deepseek prefix.

    A user who pointed ``$ANTHROPIC_BASE_URL`` at an Anthropic-compatible
    relay (Ollama's compat endpoint, an internal proxy, etc.) is opting
    in explicitly — trust the override, the same way we already do for
    ``qwen*`` and ``llama*``.
    """
    _clear_inference_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ollama")
    assert _infer_provider("deepseek-v4-pro") == "anthropic"
    assert _infer_provider("deepseek-v4-pro:cloud") == "anthropic"


def test_infer_deepseek_v4_pro_cloud_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``deepseek-v4-pro:cloud`` must resolve without raising.

    Before the prefix was added, the id fell through every branch and
    raised ``ValueError("Cannot infer provider …")`` because the catalog
    did not contain the ``:cloud`` entry either.
    """
    _clear_inference_env(monkeypatch)
    # Should not raise.
    assert _infer_provider("deepseek-v4-pro:cloud") == "ollama"


# ---------------------------------------------------------------------------
# Catalog entries
# ---------------------------------------------------------------------------


def test_catalog_has_deepseek_v4_entries() -> None:
    """The default catalog ships ``deepseek-v4`` family bindings."""
    catalog = ProviderCatalog.default()
    assert "deepseek-v4" in catalog.models
    assert "deepseek-v4-pro" in catalog.models
    assert "deepseek-v4-pro:cloud" in catalog.models


def test_catalog_deepseek_v4_targets_hosted_api() -> None:
    """Bare V4 ids point at ``https://api.deepseek.com/v1`` via compatible."""
    catalog = ProviderCatalog.default()
    cfg = catalog.get("deepseek-v4-pro")
    assert cfg is not None
    assert cfg.provider_type == "compatible"
    assert cfg.base_url == "https://api.deepseek.com/v1"
    assert cfg.api_key_env == "DEEPSEEK_API_KEY"
    assert cfg.context_window == 128_000
    assert cfg.cost == (0.435, 0.87)


def test_catalog_deepseek_v4_pro_cloud_targets_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``:cloud`` SKU resolves the Ollama daemon URL via env."""
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    catalog = ProviderCatalog.default()
    cfg = catalog.get("deepseek-v4-pro:cloud")
    assert cfg is not None
    assert cfg.provider_type == "ollama"
    assert cfg.resolve_base_url() == "http://localhost:11434"
    # 262k matches the documented cloud-tag context window for V4.
    assert cfg.context_window == 262_144


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


def test_pricing_table_contains_deepseek_v4() -> None:
    """PRICING dict carries entries for every new id."""
    assert "deepseek-v4" in PRICING
    assert "deepseek-v4-pro" in PRICING
    assert "deepseek-v4-pro:cloud" in PRICING


def test_calculate_cost_deepseek_v4_uses_the_published_pro_rate() -> None:
    """Bare ``deepseek-v4`` bills at the dearer of the two published V4 tiers.

    It is not a real SKU — it catches any future V4 id that is neither -flash
    nor -pro, and is pinned high deliberately so an unknown SKU over-bills a
    budget rather than under-billing it. Was a $0.55/$2.19 placeholder copied
    from deepseek-reasoner until DeepSeek published the V4 sheet.
    """
    cost = calculate_cost(
        "deepseek-v4",
        {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    # 0.435 + 0.87 == 1.305 USD per 1M+1M.
    assert cost == pytest.approx(1.305)


def test_calculate_cost_deepseek_v4_pro_longest_prefix() -> None:
    """``deepseek-v4-pro`` matches its own entry, not ``deepseek-v4``.

    Both carry the published Pro rate, but the test pins the longest-prefix
    behaviour so a future split doesn't silently fall back to another bucket —
    notably ``deepseek-v4-flash``, which is 3x cheaper and must never be
    reached by a ``deepseek-v4-pro`` id.
    """
    pro_input = PRICING["deepseek-v4-pro"][0]
    v4_input = PRICING["deepseek-v4"][0]
    cost_pro = calculate_cost(
        "deepseek-v4-pro",
        {"input_tokens": 1_000_000, "output_tokens": 0},
    )
    cost_v4 = calculate_cost(
        "deepseek-v4",
        {"input_tokens": 1_000_000, "output_tokens": 0},
    )
    assert cost_pro == pytest.approx(pro_input)
    assert cost_v4 == pytest.approx(v4_input)
    # Reasoner is an alias of the cheaper v4-flash tier, never the Pro rate.
    assert PRICING["deepseek-reasoner"] == (0.14, 0.28)
    assert PRICING["deepseek-v4-flash"] == (0.14, 0.28)


def test_calculate_cost_deepseek_v4_cloud_resolves() -> None:
    """``deepseek-v4-pro:cloud`` matches its own pricing tuple first."""
    cost = calculate_cost(
        "deepseek-v4-pro:cloud",
        {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    # Approximated at the first-party Pro rate; true billing is Ollama's, which
    # is why the prefix stays a deliberate PRICING_OVERRIDES entry.
    assert cost == pytest.approx(1.305)


# ---------------------------------------------------------------------------
# Optional live smoke (skipped unless daemon + tag available)
# ---------------------------------------------------------------------------


def _ollama_has_deepseek_v4(host: str) -> bool:
    """Return ``True`` iff the local Ollama daemon lists the V4 cloud tag.

    Probes ``GET {host}/api/tags`` (Ollama's stable model-listing
    endpoint). Treats any HTTP error or timeout as "not available" so
    the smoke test stays opt-in.
    """
    url = host.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=0.5) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError):
        return False
    return "deepseek-v4-pro:cloud" in body


@pytest.mark.skipif(
    not _ollama_has_deepseek_v4(
        os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
    ),
    reason="Ollama daemon unreachable or deepseek-v4-pro:cloud not pulled",
)
def test_live_smoke_deepseek_v4_pro_cloud_via_ollama() -> None:
    """Live: ask the local Ollama daemon to instantiate the V4 cloud SKU.

    Skipped unless the daemon answers on the configured ``$OLLAMA_HOST``
    AND the ``deepseek-v4-pro:cloud`` tag appears in ``/api/tags``. Only
    asserts the provider object is materialised — no completion call —
    to keep CI cheap.
    """
    pytest.importorskip("httpx")
    from chimera.providers.factory import create_provider

    provider = create_provider(model="deepseek-v4-pro:cloud")
    assert provider.model_name == "deepseek-v4-pro:cloud"
