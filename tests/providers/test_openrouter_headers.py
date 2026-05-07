"""Tests for OpenRouter cosmetic header wiring (agent P4 — wave 8).

Covers:

* ``OpenAICompatibleProvider`` accepting ``extra_headers`` and applying
  them on every request (additive over the default
  ``Content-Type`` + ``Authorization`` pair, additive over ``headers=``).
* The otter / ferret / weasel CLI provider chains setting ``HTTP-Referer``
  and ``X-Title`` when they route through OpenRouter.
* ``$OPENROUTER_REFERER`` and ``$OPENROUTER_TITLE`` env-var overrides.

The tests mock ``httpx.post`` so no network traffic occurs.
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from chimera.providers.compatible import OpenAICompatibleProvider
from chimera.types import Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_response() -> MagicMock:
    """Return a MagicMock shaped like a successful OpenAI-compat response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    return mock_response


def _captured_headers(mock_httpx: MagicMock) -> dict[str, str]:
    """Return the headers dict that was passed to the last ``httpx.post`` call."""
    assert mock_httpx.post.called, "expected httpx.post to be invoked"
    _, kwargs = mock_httpx.post.call_args
    headers = kwargs.get("headers")
    assert isinstance(headers, dict), f"expected headers dict, got {type(headers)}"
    return headers


# ---------------------------------------------------------------------------
# Provider-level: extra_headers plumbing
# ---------------------------------------------------------------------------


class TestExtraHeaders:
    def test_extra_headers_attached_to_request(self) -> None:
        """``extra_headers=`` values should appear on every outbound request."""
        with patch("chimera.providers.compatible.httpx") as mock_httpx:
            mock_httpx.post.return_value = _stub_response()

            provider = OpenAICompatibleProvider(
                model="anthropic/claude-sonnet-4",
                base_url="https://openrouter.ai/api/v1",
                api_key="test-key",
                extra_headers={
                    "HTTP-Referer": "https://example.test",
                    "X-Title": "chimera-test 0.0.1",
                },
            )
            provider.complete([Message.user("hi")])

            headers = _captured_headers(mock_httpx)
            assert headers["HTTP-Referer"] == "https://example.test"
            assert headers["X-Title"] == "chimera-test 0.0.1"
            # Defaults still present.
            assert headers["Authorization"] == "Bearer test-key"
            assert headers["Content-Type"] == "application/json"

    def test_extra_headers_default_none_keeps_legacy_shape(self) -> None:
        """Without ``extra_headers``, only the two defaults are sent."""
        with patch("chimera.providers.compatible.httpx") as mock_httpx:
            mock_httpx.post.return_value = _stub_response()

            provider = OpenAICompatibleProvider(
                model="x",
                base_url="https://example.invalid/v1",
                api_key="k",
            )
            provider.complete([Message.user("hi")])

            headers = _captured_headers(mock_httpx)
            assert set(headers.keys()) == {"Content-Type", "Authorization"}

    def test_extra_headers_override_headers(self) -> None:
        """``extra_headers`` is merged AFTER ``headers``, so it wins on collision."""
        with patch("chimera.providers.compatible.httpx") as mock_httpx:
            mock_httpx.post.return_value = _stub_response()

            provider = OpenAICompatibleProvider(
                model="x",
                base_url="https://example.invalid/v1",
                api_key="k",
                headers={"X-Title": "from-headers"},
                extra_headers={"X-Title": "from-extra"},
            )
            provider.complete([Message.user("hi")])

            headers = _captured_headers(mock_httpx)
            assert headers["X-Title"] == "from-extra"


# ---------------------------------------------------------------------------
# CLI chains: otter / ferret / weasel route OpenRouter with cosmetic headers
# ---------------------------------------------------------------------------


@pytest.fixture
def openrouter_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set OPENROUTER_API_KEY and clear competing keys so chains pick OR."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    for var in (
        "OTTER_MODEL",
        "FERRET_MODEL",
        "WEASEL_MODEL",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "LLAMACPP_API_KEY",
        "OLLAMA_API_KEY",
        "OPENROUTER_REFERER",
        "OPENROUTER_TITLE",
    ):
        monkeypatch.delenv(var, raising=False)


def _ns(model: str = "anthropic/claude-sonnet-4") -> argparse.Namespace:
    """Build a minimal argparse namespace acceptable to build_provider()."""
    return argparse.Namespace(model=model, no_color=False, max_tokens=None)


@pytest.mark.parametrize(
    ("module_path", "expected_title"),
    [
        ("chimera.otter.providers", "chimera otter 0.6.0"),
        ("chimera.ferret.providers", "chimera ferret 0.6.0"),
        ("chimera.weasel.providers", "chimera weasel 0.6.0"),
    ],
)
def test_cli_chain_sets_openrouter_headers(
    openrouter_env: None,
    module_path: str,
    expected_title: str,
) -> None:
    """Each CLI's build_provider should set HTTP-Referer + X-Title for OpenRouter."""
    import importlib

    module = importlib.import_module(module_path)
    args = _ns()

    with patch("chimera.providers.compatible.httpx") as mock_httpx:
        mock_httpx.post.return_value = _stub_response()
        provider = module.build_provider(args)
        # Drive a request so the headers are observable on httpx.post.
        provider.complete([Message.user("hi")])

        headers = _captured_headers(mock_httpx)
        assert headers["HTTP-Referer"] == "https://github.com/0bserver07/chimera"
        assert headers["X-Title"] == expected_title


@pytest.mark.parametrize(
    "module_path",
    [
        "chimera.otter.providers",
        "chimera.ferret.providers",
        "chimera.weasel.providers",
    ],
)
def test_env_overrides_referer_and_title(
    openrouter_env: None,
    monkeypatch: pytest.MonkeyPatch,
    module_path: str,
) -> None:
    """``$OPENROUTER_REFERER`` / ``$OPENROUTER_TITLE`` should win over defaults."""
    import importlib

    monkeypatch.setenv("OPENROUTER_REFERER", "https://my-app.test")
    monkeypatch.setenv("OPENROUTER_TITLE", "my-app 1.2.3")

    module = importlib.import_module(module_path)
    args = _ns()

    with patch("chimera.providers.compatible.httpx") as mock_httpx:
        mock_httpx.post.return_value = _stub_response()
        provider = module.build_provider(args)
        provider.complete([Message.user("hi")])

        headers = _captured_headers(mock_httpx)
        assert headers["HTTP-Referer"] == "https://my-app.test"
        assert headers["X-Title"] == "my-app 1.2.3"
