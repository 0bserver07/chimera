"""Tests for :mod:`chimera.errors.friendly`.

Validates that synthetic provider / network exceptions get mapped to
:class:`ChimeraUserError` with the right message, hint, and category,
that the :func:`friendly_errors` decorator prints + returns the right
exit code, and that ``--debug`` causes the original exception to
propagate unaltered.
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from chimera.errors import (
    ChimeraUserError,
    friendly_errors,
    wrap_provider_errors,
)


# ---------------------------------------------------------------------------
# Synthetic exception fixtures
# ---------------------------------------------------------------------------


def _make_anthropic_auth_error() -> Exception:
    """Return a real (or shaped) ``anthropic.AuthenticationError``.

    Falls back to a name-matching shim if instantiation requires args
    we don't want to mock — the wrapper recognises both shapes.
    """
    try:
        import anthropic

        # The SDK's AuthenticationError requires response/body args, so
        # we subclass it with a no-arg ctor for the test.
        class _AuthErr(anthropic.AuthenticationError):  # type: ignore[misc]
            def __init__(self) -> None:  # noqa: D401 — test shim.
                Exception.__init__(self, "no api key")

        return _AuthErr()
    except Exception:
        class AuthenticationError(Exception):
            pass

        return AuthenticationError("no api key")


def _make_openai_auth_error() -> Exception:
    try:
        import openai

        class _AuthErr(openai.AuthenticationError):  # type: ignore[misc]
            def __init__(self) -> None:
                Exception.__init__(self, "no api key")

        return _AuthErr()
    except Exception:
        class AuthenticationError(Exception):
            pass

        return AuthenticationError("no api key")


def _make_connect_error(url: str) -> Exception:
    """Build an ``httpx.ConnectError`` with the right ``request.url``."""
    import httpx

    request = httpx.Request("POST", url)
    return httpx.ConnectError("connection refused", request=request)


def _make_status_error(code: int) -> Exception:
    """Build an ``httpx.HTTPStatusError`` carrying ``code``."""
    import httpx

    request = httpx.Request("POST", "https://api.example.com/v1/messages")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {code}", request=request, response=response
    )


def _make_factory_value_error(model: str = "made-up-model") -> ValueError:
    """Mimic the message :func:`chimera.providers.factory` raises."""
    return ValueError(
        f"Cannot infer provider from model name '{model}'.\n"
        "Options:\n  1. Set ANTHROPIC_BASE_URL ...\n"
    )


# ---------------------------------------------------------------------------
# wrap_provider_errors: mapping
# ---------------------------------------------------------------------------


def test_anthropic_authentication_error_maps_to_auth_category() -> None:
    with pytest.raises(ChimeraUserError) as info:
        with wrap_provider_errors():
            raise _make_anthropic_auth_error()
    err = info.value
    assert err.category == "auth"
    assert "no valid API key" in err.message
    assert "chimera auth login" in err.hint
    assert "chimera doctor" in err.hint


def test_openai_authentication_error_maps_to_auth_category() -> None:
    with pytest.raises(ChimeraUserError) as info:
        with wrap_provider_errors():
            raise _make_openai_auth_error()
    err = info.value
    assert err.category == "auth"
    assert "chimera auth login" in err.hint


@pytest.mark.parametrize(
    "url,daemon",
    [
        ("http://localhost:11434/api/generate", "Ollama"),
        ("http://127.0.0.1:8888/completion", "llama.cpp"),
        ("http://localhost:8000/v1/chat", "vLLM"),
        ("http://0.0.0.0:30000/generate", "SGLang"),
    ],
)
def test_connect_error_to_known_local_daemon(url: str, daemon: str) -> None:
    with pytest.raises(ChimeraUserError) as info:
        with wrap_provider_errors():
            raise _make_connect_error(url)
    err = info.value
    assert err.category == "connect"
    assert daemon in err.message
    assert "not running" in err.message
    assert err.hint  # has a setup hint


def test_connect_error_to_unknown_host_still_friendly() -> None:
    with pytest.raises(ChimeraUserError) as info:
        with wrap_provider_errors():
            raise _make_connect_error("https://api.example.com/v1/messages")
    err = info.value
    assert err.category == "connect"
    assert "Cannot reach upstream" in err.message
    assert "chimera doctor" in err.hint


def test_factory_value_error_maps_to_routing() -> None:
    with pytest.raises(ChimeraUserError) as info:
        with wrap_provider_errors():
            raise _make_factory_value_error("frobozz-9000")
    err = info.value
    assert err.category == "routing"
    assert "frobozz-9000" in err.message
    assert "didn't match any provider chain" in err.message
    assert "claude-*" in err.hint  # suggestion list present


def test_unrelated_value_error_passes_through() -> None:
    with pytest.raises(ValueError) as info:
        with wrap_provider_errors():
            raise ValueError("not a provider error")
    assert "not a provider error" in str(info.value)


@pytest.mark.parametrize("code", [401, 403])
def test_http_status_401_403_maps_to_auth(code: int) -> None:
    with pytest.raises(ChimeraUserError) as info:
        with wrap_provider_errors():
            raise _make_status_error(code)
    err = info.value
    assert err.category == "auth"
    assert str(code) in err.message
    assert "chimera auth login" in err.hint


def test_http_status_429_maps_to_rate_limit() -> None:
    with pytest.raises(ChimeraUserError) as info:
        with wrap_provider_errors():
            raise _make_status_error(429)
    err = info.value
    assert err.category == "rate_limit"
    assert "Rate-limited" in err.message
    assert "30s" in err.hint or "30 s" in err.hint


@pytest.mark.parametrize("code", [500, 502, 503, 504])
def test_http_status_5xx_maps_to_upstream(code: int) -> None:
    with pytest.raises(ChimeraUserError) as info:
        with wrap_provider_errors():
            raise _make_status_error(code)
    err = info.value
    assert err.category == "upstream"
    assert str(code) in err.message


def test_unrelated_exception_passes_through() -> None:
    class CustomError(Exception):
        pass

    with pytest.raises(CustomError):
        with wrap_provider_errors():
            raise CustomError("not handled")


def test_already_friendly_passes_through_unchanged() -> None:
    original = ChimeraUserError("hello", hint="world", category="auth")
    with pytest.raises(ChimeraUserError) as info:
        with wrap_provider_errors():
            raise original
    assert info.value is original


# ---------------------------------------------------------------------------
# friendly_errors decorator
# ---------------------------------------------------------------------------


def _ns(**kw: Any) -> argparse.Namespace:
    return argparse.Namespace(**kw)


def test_decorator_returns_exit_code_and_prints(capsys: pytest.CaptureFixture[str]) -> None:
    @friendly_errors
    def run(args: argparse.Namespace) -> int:
        raise _make_factory_value_error("xyz")

    rc = run(_ns(debug=False))
    assert rc == 1
    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert "xyz" in captured.err
    assert "hint:" in captured.err


def test_decorator_uses_custom_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    @friendly_errors
    def run(args: argparse.Namespace) -> int:
        raise ChimeraUserError("boom", hint="fix it", exit_code=42)

    rc = run(_ns(debug=False))
    assert rc == 42


def test_decorator_passes_through_when_debug_true() -> None:
    @friendly_errors
    def run(args: argparse.Namespace) -> int:
        raise _make_factory_value_error("xyz")

    with pytest.raises(ValueError) as info:
        run(_ns(debug=True))
    assert "Cannot infer provider" in str(info.value)


def test_decorator_passes_through_anthropic_when_debug_true() -> None:
    @friendly_errors
    def run(args: argparse.Namespace) -> int:
        raise _make_anthropic_auth_error()

    with pytest.raises(Exception) as info:
        run(_ns(debug=True))
    # Original AuthenticationError is preserved (not ChimeraUserError).
    assert not isinstance(info.value, ChimeraUserError)


def test_decorator_no_args_no_debug_attr_defaults_to_friendly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Args missing ``debug`` should default to friendly-error path."""

    @friendly_errors
    def run(args: argparse.Namespace) -> int:
        raise _make_factory_value_error("nope")

    rc = run(argparse.Namespace())  # no debug attribute at all
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_decorator_returns_zero_on_success() -> None:
    @friendly_errors
    def run(args: argparse.Namespace) -> int:
        return 0

    assert run(_ns(debug=False)) == 0


def test_decorator_preserves_args_and_kwargs() -> None:
    captured: dict[str, Any] = {}

    @friendly_errors
    def run(args: argparse.Namespace, extra: int = 0) -> int:
        captured["extra"] = extra
        return 7

    rc = run(_ns(debug=False), extra=99)
    assert rc == 7
    assert captured["extra"] == 99


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def test_friendly_output_no_color_when_not_a_tty(
    capsys: pytest.CaptureFixture[str],
) -> None:
    @friendly_errors
    def run(args: argparse.Namespace) -> int:
        raise ChimeraUserError(
            "msg here", hint="line one\nline two", category="auth"
        )

    run(_ns(debug=False))
    err = capsys.readouterr().err
    # capsys streams aren't TTYs → no ANSI escape sequences should appear.
    assert "\x1b[" not in err
    assert "msg here" in err
    assert "line one" in err
    assert "line two" in err


def test_chimera_user_error_str_returns_message() -> None:
    err = ChimeraUserError("the message", hint="ignored")
    assert str(err) == "the message"


def test_chimera_user_error_default_exit_code_is_one() -> None:
    err = ChimeraUserError("msg")
    assert err.exit_code == 1
    assert err.category == "unknown"
