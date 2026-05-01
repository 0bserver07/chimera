"""Friendly diagnostic wrappers for provider / network errors.

Maps raw provider SDK exceptions (``anthropic``, ``openai``, ``httpx``)
and provider-factory ``ValueError``\\s into :class:`ChimeraUserError`
instances. Each error carries a short, single-line message plus a
multi-line remediation hint that points users at the right next step
(``chimera doctor``, ``chimera auth login``, starting a local daemon,
adjusting model id, retrying, etc.).

The :func:`friendly_errors` decorator is applied to each CLI's
``run(args)`` entry point. When ``args.debug`` is truthy the wrapper
no-ops and the raw exception bubbles up so users see a full traceback;
otherwise the friendly message is printed (colored when stderr is a
TTY) and the decorated function returns ``e.exit_code``.

The implementation deliberately uses ``getattr`` / lazy imports for the
provider SDKs so that this module remains import-safe even when an
optional extra (e.g. ``[anthropic]``, ``[openai]``) is missing — we
fall back to ``type(...).__name__`` matching when the SDK class is
unimportable.
"""

from __future__ import annotations

import functools
import sys
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar

__all__ = [
    "ChimeraUserError",
    "friendly_errors",
    "wrap_provider_errors",
]


F = TypeVar("F", bound=Callable[..., int])


# ---------------------------------------------------------------------------
# Local daemon hints (host:port → human-readable description + setup hint).
# ---------------------------------------------------------------------------
#
# WHY: ConnectError to a known local LLM daemon port is one of the most
# common first-run failures. Having a tight port→name table lets us
# surface a precise "Ollama at http://localhost:11434 not running"
# message instead of "ConnectError: All connection attempts failed".

_LOCAL_DAEMONS: dict[int, tuple[str, str]] = {
    11434: (
        "Ollama",
        "Start it with `ollama serve` (or open the Ollama.app). "
        "See https://ollama.com/download.",
    ),
    8888: (
        "llama.cpp",
        "Start the llama.cpp server: "
        "`./server -m path/to/model.gguf --port 8888`.",
    ),
    8000: (
        "vLLM",
        "Start the vLLM OpenAI-compatible server: "
        "`vllm serve <model> --port 8000`.",
    ),
    30000: (
        "SGLang",
        "Start the SGLang server: "
        "`python -m sglang.launch_server --model-path <model> --port 30000`.",
    ),
}


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class ChimeraUserError(Exception):
    """Friendly wrapper for end-user-facing failures.

    Attributes:
        message: Short single-line description of what went wrong.
        hint: Multi-line remediation hint (commands to try, doc links).
        category: Machine-readable category — one of ``auth``, ``connect``,
            ``routing``, ``rate_limit``, ``upstream``, ``unknown``.
        exit_code: Process exit code the CLI should return. Defaults to 1.
    """

    def __init__(
        self,
        message: str,
        *,
        hint: str = "",
        category: str = "unknown",
        exit_code: int = 1,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.category = category
        self.exit_code = exit_code

    def __str__(self) -> str:
        return self.message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_authentication_error(exc: BaseException) -> bool:
    """Return True if ``exc`` is an SDK ``AuthenticationError``.

    We try real ``isinstance`` checks against the SDK classes when
    importable; otherwise fall back to comparing ``type(exc).__name__``
    so this stays import-safe in environments where ``anthropic`` /
    ``openai`` aren't installed.
    """
    try:
        import anthropic  # type: ignore[import-not-found]

        if isinstance(exc, anthropic.AuthenticationError):
            return True
    except Exception:  # noqa: BLE001 — defensive: SDK absent or broken.
        pass
    try:
        import openai  # type: ignore[import-not-found]

        if isinstance(exc, openai.AuthenticationError):
            return True
    except Exception:  # noqa: BLE001
        pass
    # Name-based fallback (e.g. SDK installed under a different alias).
    return type(exc).__name__ == "AuthenticationError"


def _looks_like_local_daemon_url(url: str) -> tuple[str, int] | None:
    """Return ``(daemon_name, port)`` if ``url`` is a known local daemon.

    Recognises ``localhost`` / ``127.0.0.1`` / ``0.0.0.0`` on the four
    canonical ports (11434/8888/8000/30000). Returns ``None`` otherwise.
    """
    if not url:
        return None
    lowered = url.lower()
    is_local = (
        "localhost" in lowered
        or "127.0.0.1" in lowered
        or "0.0.0.0" in lowered
    )
    if not is_local:
        return None
    for port, (name, _) in _LOCAL_DAEMONS.items():
        if f":{port}" in lowered:
            return (name, port)
    return None


def _connect_error_url(exc: BaseException) -> str:
    """Best-effort extraction of the request URL from an ``httpx`` exc."""
    request = getattr(exc, "request", None)
    if request is not None:
        url = getattr(request, "url", None)
        if url is not None:
            return str(url)
    # Some SDKs stash the URL on ``args[0]`` or similar — fall back to str.
    return str(exc)


def _http_status_code(exc: BaseException) -> int | None:
    """Pull the HTTP status code from an ``httpx.HTTPStatusError``-like exc."""
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status
    # Fallback: scan the message for "401"/"403"/"429"/etc.
    return None


def _classify_value_error(exc: ValueError) -> ChimeraUserError | None:
    """Detect provider-factory routing errors by their message text.

    The factory raises ``ValueError("Cannot infer provider from model
    name '<x>'.\\nOptions:\\n …")``; we collapse that into a one-line
    message plus a curated suggestion list.
    """
    text = str(exc)
    if "Cannot infer provider" not in text:
        return None
    # Pull the model id out of the quoted slot.
    model = ""
    if "'" in text:
        try:
            model = text.split("'", 2)[1]
        except IndexError:
            model = ""
    msg = (
        f"Model id '{model}' didn't match any provider chain."
        if model
        else "Model id didn't match any provider chain."
    )
    hint = (
        "Try one of:\n"
        "  * Use a known prefix: claude-*, gpt-*, gemini-*, glm-*, "
        "kimi-*, grok-*, llama*, qwen*, mistral*, phi*, vllm/*, sglang/*\n"
        "  * Pass --provider anthropic|openai|google|ollama explicitly\n"
        "  * Set ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN for an "
        "Anthropic-compatible endpoint\n"
        "  * Run `chimera doctor` to inspect which providers are reachable"
    )
    return ChimeraUserError(msg, hint=hint, category="routing")


def _classify_connect_error(exc: BaseException) -> ChimeraUserError:
    """Build a friendly message for an ``httpx.ConnectError``."""
    url = _connect_error_url(exc)
    daemon = _looks_like_local_daemon_url(url)
    if daemon is not None:
        name, port = daemon
        _, setup = _LOCAL_DAEMONS[port]
        msg = (
            f"{name} daemon at http://localhost:{port} not running "
            "(connection refused)."
        )
        hint = setup + "\nRun `chimera doctor` to confirm reachability."
        return ChimeraUserError(msg, hint=hint, category="connect")
    msg = f"Cannot reach upstream at {url} (connection refused)."
    hint = (
        "Check that the host is up and reachable.\n"
        "Run `chimera doctor` to inspect provider connectivity."
    )
    return ChimeraUserError(msg, hint=hint, category="connect")


def _classify_http_status(exc: BaseException) -> ChimeraUserError | None:
    """Map ``httpx.HTTPStatusError`` to auth / rate-limit / upstream."""
    code = _http_status_code(exc)
    if code is None:
        return None
    if code in (401, 403):
        return ChimeraUserError(
            f"Auth rejected by upstream (HTTP {code}) — check key validity.",
            hint=(
                "Re-issue the key with `chimera auth login`, then\n"
                "verify with `chimera doctor`."
            ),
            category="auth",
        )
    if code == 429:
        return ChimeraUserError(
            "Rate-limited by upstream (HTTP 429).",
            hint=(
                "Wait ~30s and retry, or switch model with --model. "
                "Persistent 429s usually mean a quota cap — check the "
                "provider dashboard."
            ),
            category="rate_limit",
        )
    if 500 <= code < 600:
        return ChimeraUserError(
            f"Upstream issue (HTTP {code}).",
            hint=(
                "Retry shortly, or switch model with --model. "
                "If it persists, check the provider's status page."
            ),
            category="upstream",
        )
    return None


# ---------------------------------------------------------------------------
# Context manager / decorator
# ---------------------------------------------------------------------------


@contextmanager
def wrap_provider_errors() -> Iterator[None]:
    """Convert provider / network exceptions into :class:`ChimeraUserError`.

    Catches:

    * ``anthropic.AuthenticationError`` / ``openai.AuthenticationError``
      → "no API key" + ``chimera auth login`` / ``chimera doctor`` hint.
    * ``httpx.ConnectError`` to ``localhost:11434/8888/8000/30000`` →
      "<daemon> at <url> not running" + the daemon-specific setup
      command. Other ConnectErrors still get a generic-but-useful
      message.
    * ``ValueError`` from the provider factory ("Cannot infer provider
      …") → one-line message + curated suggestion list.
    * ``httpx.HTTPStatusError`` 401/403 → auth-rejected.
    * ``httpx.HTTPStatusError`` 429 → rate-limited.
    * ``httpx.HTTPStatusError`` 5xx → upstream issue.

    Anything we don't recognise is re-raised unchanged.
    """
    try:
        yield
    except ChimeraUserError:
        # Already friendly — let it propagate.
        raise
    except ValueError as exc:
        friendly = _classify_value_error(exc)
        if friendly is not None:
            raise friendly from exc
        raise
    except BaseException as exc:
        # Auth (anthropic / openai SDKs) — match before generic httpx
        # branches because both SDKs subclass httpx errors.
        if _is_authentication_error(exc):
            friendly = ChimeraUserError(
                "Provider rejected request — no valid API key found.",
                hint=(
                    "Sign in with `chimera auth login`, or set the\n"
                    "right env var (ANTHROPIC_API_KEY / OPENAI_API_KEY /\n"
                    "GOOGLE_API_KEY). Run `chimera doctor` to see which\n"
                    "providers Chimera currently has credentials for."
                ),
                category="auth",
            )
            raise friendly from exc

        # httpx ConnectError — local daemon not running, etc.
        try:
            import httpx
        except Exception:  # noqa: BLE001 — httpx absent.
            httpx = None  # type: ignore[assignment]

        if httpx is not None and isinstance(exc, httpx.ConnectError):
            raise _classify_connect_error(exc) from exc

        if httpx is not None and isinstance(exc, httpx.HTTPStatusError):
            friendly_http = _classify_http_status(exc)
            if friendly_http is not None:
                raise friendly_http from exc

        # Name-based fallback for httpx errors (when the import failed
        # but the exception class is still recognisable).
        name = type(exc).__name__
        if name == "ConnectError":
            raise _classify_connect_error(exc) from exc
        if name == "HTTPStatusError":
            friendly_http = _classify_http_status(exc)
            if friendly_http is not None:
                raise friendly_http from exc

        # Not one we handle — re-raise verbatim.
        raise


# ---------------------------------------------------------------------------
# Decorator + printing
# ---------------------------------------------------------------------------


def _supports_color(stream: Any) -> bool:
    """True when the stream looks like a TTY that supports ANSI color."""
    try:
        return bool(stream.isatty())
    except Exception:  # noqa: BLE001
        return False


def _print_friendly(err: ChimeraUserError, stream: Any = None) -> None:
    """Render ``err`` to ``stream`` (default: ``sys.stderr``).

    Uses ANSI red for the message and dim grey for the hint when the
    stream is a TTY; otherwise emits plain text.
    """
    if stream is None:
        stream = sys.stderr
    if _supports_color(stream):
        red = "\x1b[31m"
        dim = "\x1b[2m"
        reset = "\x1b[0m"
        print(f"{red}error:{reset} {err.message}", file=stream)
        if err.hint:
            for line in err.hint.splitlines():
                print(f"{dim}  hint: {line}{reset}", file=stream)
    else:
        print(f"error: {err.message}", file=stream)
        if err.hint:
            for line in err.hint.splitlines():
                print(f"  hint: {line}", file=stream)


def friendly_errors(func: F) -> F:
    """Decorate a CLI ``run(args)`` entry point with friendly-error handling.

    When the wrapped function raises a recognised provider /
    network exception, it's converted into a :class:`ChimeraUserError`,
    printed to stderr (colored if the stream is a TTY), and the
    decorator returns ``err.exit_code``.

    If ``args.debug`` is truthy, the wrapper is a no-op — the original
    exception bubbles up unaltered so the user sees a full traceback.

    Args:
        func: A callable matching ``run(args: argparse.Namespace) -> int``.

    Returns:
        The decorated callable.
    """

    @functools.wraps(func)
    def wrapper(args: Any, *rest: Any, **kwargs: Any) -> int:
        debug = bool(getattr(args, "debug", False))
        if debug:
            # Pass through — no friendly translation, no swallowed traceback.
            return func(args, *rest, **kwargs)
        try:
            with wrap_provider_errors():
                return func(args, *rest, **kwargs)
        except ChimeraUserError as err:
            _print_friendly(err)
            return err.exit_code

    return wrapper  # type: ignore[return-value]
