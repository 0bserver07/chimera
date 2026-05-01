"""Tests for ``chimera.providers.xai`` (agent P2, wave 8).

xAI is OpenAI-compatible at ``https://api.x.ai/v1``. We test:

* The factory routes ``grok-*`` model ids to the ``xai`` provider type.
* The ``xai`` provider hits ``api.x.ai/v1`` with a Bearer auth header.
* Explicit ``provider_type="xai"`` works without prefix inference.
* ``$XAI_API_KEY`` and ``$GROK_API_KEY`` are both honoured.
* Cost lookups land on the ``grok-*`` pricing entries, with longest-prefix
  matching (so ``grok-3-mini`` doesn't bill at the ``grok-3`` rate).

Every test mocks :mod:`httpx` — no live calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chimera.providers import xai as xai_module
from chimera.providers.cost import calculate_cost
from chimera.providers.factory import _infer_provider, create_provider
from chimera.types import Message


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_httpx() -> MagicMock:
    """Patch :mod:`httpx` inside :mod:`chimera.providers.compatible`.

    The xAI provider delegates to :class:`OpenAICompatibleProvider`, which
    is the layer that actually imports ``httpx``. Patching it once gives
    every test a deterministic 200 response shape.
    """
    with patch("chimera.providers.compatible.httpx") as m:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        m.post.return_value = mock_response
        yield m


@pytest.fixture
def clear_xai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip xAI-relevant env vars so each test starts clean."""
    for var in ("XAI_API_KEY", "GROK_API_KEY"):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Factory inference + routing
# ---------------------------------------------------------------------------


def test_infer_provider_grok_prefix_routes_to_xai() -> None:
    """``grok-*`` model ids resolve to the ``xai`` provider type."""
    assert _infer_provider("grok-3") == "xai"
    assert _infer_provider("grok-3-mini") == "xai"
    assert _infer_provider("grok-4") == "xai"
    assert _infer_provider("GROK-3") == "xai"


def test_create_provider_grok_prefix(
    mock_httpx: MagicMock,
    clear_xai_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``create_provider(model="grok-3")`` returns an xAI-targeted provider."""
    monkeypatch.setenv("XAI_API_KEY", "xai-test-123")
    provider = create_provider(model="grok-3")

    assert provider.model_name == "grok-3"
    # Inspect the underlying compatible provider's base URL + key.
    assert provider._base_url == "https://api.x.ai/v1"  # noqa: SLF001
    assert provider._api_key == "xai-test-123"  # noqa: SLF001


def test_create_provider_explicit_xai_type(
    mock_httpx: MagicMock,
    clear_xai_env: None,
) -> None:
    """``provider_type="xai"`` works without relying on prefix inference."""
    provider = create_provider(
        provider_type="xai",
        model="grok-3-mini",
        api_key="xai-explicit",
    )
    assert provider.model_name == "grok-3-mini"
    assert provider._base_url == "https://api.x.ai/v1"  # noqa: SLF001
    assert provider._api_key == "xai-explicit"  # noqa: SLF001


# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------


def test_explicit_key_wins_over_env(
    mock_httpx: MagicMock,
    clear_xai_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit ``api_key`` argument beats every env var."""
    monkeypatch.setenv("XAI_API_KEY", "from-env")
    provider = xai_module.create_xai_provider(
        model="grok-3", api_key="from-arg",
    )
    assert provider._api_key == "from-arg"  # noqa: SLF001


def test_xai_api_key_env_resolved(
    mock_httpx: MagicMock,
    clear_xai_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$XAI_API_KEY`` is picked up when no explicit key is passed."""
    monkeypatch.setenv("XAI_API_KEY", "xai-from-env")
    provider = xai_module.create_xai_provider(model="grok-3")
    assert provider._api_key == "xai-from-env"  # noqa: SLF001


def test_grok_api_key_env_resolved(
    mock_httpx: MagicMock,
    clear_xai_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$GROK_API_KEY`` is honoured as a synonym for ``$XAI_API_KEY``."""
    monkeypatch.setenv("GROK_API_KEY", "grok-from-env")
    provider = xai_module.create_xai_provider(model="grok-3")
    assert provider._api_key == "grok-from-env"  # noqa: SLF001


def test_xai_api_key_preferred_over_grok(
    mock_httpx: MagicMock,
    clear_xai_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both env vars are set, ``$XAI_API_KEY`` wins (canonical name)."""
    monkeypatch.setenv("XAI_API_KEY", "xai-preferred")
    monkeypatch.setenv("GROK_API_KEY", "grok-fallback")
    provider = xai_module.create_xai_provider(model="grok-3")
    assert provider._api_key == "xai-preferred"  # noqa: SLF001


# ---------------------------------------------------------------------------
# Wire shape: URL + auth header sent on the first request
# ---------------------------------------------------------------------------


def test_request_url_and_auth_header(
    mock_httpx: MagicMock,
    clear_xai_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the request URL and ``Authorization`` header on a real call.

    This is the only behavioural test that exercises the full call path.
    httpx is patched, so no live network — we just inspect the kwargs the
    provider sends.
    """
    monkeypatch.setenv("XAI_API_KEY", "xai-secret")
    provider = create_provider(model="grok-3")

    provider.complete([Message.user("ping")])

    assert mock_httpx.post.call_count == 1
    call_args = mock_httpx.post.call_args
    url = call_args[0][0] if call_args[0] else call_args[1].get("endpoint")
    # First positional is the endpoint URL.
    assert url == "https://api.x.ai/v1/chat/completions"

    headers = call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer xai-secret"
    assert headers["Content-Type"] == "application/json"

    payload = call_args[1]["json"]
    assert payload["model"] == "grok-3"


def test_base_url_override(
    mock_httpx: MagicMock,
    clear_xai_env: None,
) -> None:
    """``base_url`` arg overrides the default ``https://api.x.ai/v1``.

    Useful for proxies and integration tests that point at a recorder.
    """
    provider = xai_module.create_xai_provider(
        model="grok-3",
        api_key="k",
        base_url="https://proxy.example/v1",
    )
    assert provider._base_url == "https://proxy.example/v1"  # noqa: SLF001


# ---------------------------------------------------------------------------
# Context window + model defaults
# ---------------------------------------------------------------------------


def test_context_window_known_model(
    mock_httpx: MagicMock,
    clear_xai_env: None,
) -> None:
    """Known Grok ids carry their published context windows."""
    p3 = xai_module.create_xai_provider(model="grok-3", api_key="k")
    p3_mini = xai_module.create_xai_provider(model="grok-3-mini", api_key="k")
    p4 = xai_module.create_xai_provider(model="grok-4", api_key="k")

    assert p3.context_window == 131_072
    assert p3_mini.context_window == 131_072
    assert p4.context_window == 256_000


def test_context_window_unknown_model_default(
    mock_httpx: MagicMock,
    clear_xai_env: None,
) -> None:
    """Unknown Grok ids fall back to the safe 131k default."""
    p = xai_module.create_xai_provider(
        model="grok-5-future", api_key="k",
    )
    assert p.context_window == 131_072


def test_context_window_explicit_override(
    mock_httpx: MagicMock,
    clear_xai_env: None,
) -> None:
    """Explicit ``context_length`` beats both catalog and default."""
    p = xai_module.create_xai_provider(
        model="grok-3", api_key="k", context_length=42,
    )
    assert p.context_window == 42


# ---------------------------------------------------------------------------
# Cost lookup
# ---------------------------------------------------------------------------


def test_cost_grok_3() -> None:
    """``grok-3`` bills at $3/$15 per million tokens."""
    cost = calculate_cost(
        "grok-3", {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    # 3 + 15 = 18 USD per 1M+1M.
    assert cost == pytest.approx(18.0)


def test_cost_grok_3_mini_longest_prefix_wins() -> None:
    """``grok-3-mini`` matches its own prefix, not the ``grok-3`` one."""
    cost = calculate_cost(
        "grok-3-mini",
        {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    # 0.30 + 0.50 = 0.80 USD per 1M+1M (NOT 18 USD).
    assert cost == pytest.approx(0.80)


def test_cost_grok_4() -> None:
    """``grok-4`` carries its own pricing entry."""
    cost = calculate_cost(
        "grok-4", {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    # 5 + 25 = 30 USD per 1M+1M.
    assert cost == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_xai_base_url_constant() -> None:
    """The ``XAI_BASE_URL`` export must remain ``api.x.ai/v1``.

    Downstream callers (e.g. CLI provider chains, future MCP servers) may
    import this constant directly to reference the upstream endpoint.
    """
    assert xai_module.XAI_BASE_URL == "https://api.x.ai/v1"


def test_xai_provider_registered() -> None:
    """The ``xai`` provider type must be in the registry after import."""
    from chimera.providers.registry import (
        _ensure_builtins_registered,
        get_provider_factory,
    )
    _ensure_builtins_registered()
    assert get_provider_factory("xai") is not None
