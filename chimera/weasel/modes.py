"""Four-mode dispatcher for the ``chimera weasel`` subcommand.

Weasel exposes the same agent four ways:

* ``WeaselMode.INTERACTIVE`` — TTY REPL (delegates to :mod:`chimera.weasel.repl`).
* ``WeaselMode.PRINT`` — one-shot ``-p "..."`` prompt with ``--json`` toggle.
* ``WeaselMode.RPC`` — JSON-RPC 2.0 over stdin/stdout
  (delegates to :mod:`chimera.weasel.rpc`).
* ``WeaselMode.SDK`` — embeddable :class:`chimera.weasel.sdk.Agent`; the CLI
  itself never lands here at runtime, but the enum value lets us name the
  fourth surface uniformly.

The dispatcher is intentionally lazy: each mode imports its runner only when
selected so that, for example, importing ``chimera.weasel.modes`` from the
SDK does not pull in the REPL's readline / select machinery.

Example:
    >>> import argparse
    >>> ns = argparse.Namespace(mode="rpc")
    >>> # dispatch_mode(ns)  # would block on stdin in RPC mode
"""
from __future__ import annotations

import enum
from typing import Any, Callable


class WeaselMode(enum.Enum):
    """Operating mode for ``chimera weasel``.

    Attributes:
        INTERACTIVE: TTY REPL.
        PRINT: One-shot ``-p`` prompt (text or JSON output).
        RPC: JSON-RPC 2.0 over stdin/stdout.
        SDK: Embeddable agent surface (not selected by the CLI directly).
    """

    INTERACTIVE = "interactive"
    PRINT = "print"
    RPC = "rpc"
    SDK = "sdk"

    @classmethod
    def from_args(cls, args: Any) -> "WeaselMode":
        """Resolve a :class:`WeaselMode` from a parsed argparse namespace.

        Resolution order:

        1. ``args.mode`` if explicitly provided (``interactive`` / ``print`` /
           ``rpc`` / ``sdk``).
        2. Presence of ``args.prompt`` (``-p``) implies :attr:`PRINT`.
        3. Default: :attr:`INTERACTIVE`.

        Args:
            args: Parsed argparse namespace (or any object with ``mode`` /
                ``prompt`` attributes).

        Returns:
            The resolved :class:`WeaselMode`.

        Raises:
            ValueError: If ``args.mode`` is set but unrecognised.
        """
        explicit = getattr(args, "mode", None)
        if explicit:
            try:
                return cls(explicit)
            except ValueError as e:
                raise ValueError(
                    f"Unknown weasel mode: {explicit!r}. "
                    f"Expected one of {[m.value for m in cls]}."
                ) from e

        if getattr(args, "prompt", None):
            return cls.PRINT

        return cls.INTERACTIVE


def dispatch_mode(args: Any) -> int:
    """Route *args* to the appropriate weasel runner.

    The dispatcher imports each runner lazily so that selecting one mode
    does not pay the import cost of the others. Unknown / SDK modes return
    a non-zero status without raising — the SDK is meant to be used
    programmatically, not via this dispatcher.

    Args:
        args: Parsed argparse namespace from ``chimera weasel`` (or any
            object with the same attributes).

    Returns:
        Process exit code: ``0`` on success, non-zero on error.
    """
    mode = WeaselMode.from_args(args)
    runner = _RUNNERS.get(mode)
    if runner is None:
        # SDK is not a CLI-dispatched mode.
        return 2
    return runner(args)


def _run_interactive(args: Any) -> int:
    """Lazy import + delegate to the REPL runner.

    Args:
        args: Parsed CLI args.

    Returns:
        Exit code from the REPL runner, or ``0`` if no REPL is available.
    """
    try:
        from chimera.weasel import repl  # type: ignore[attr-defined]
    except ImportError:
        # W1 hasn't landed yet; surface a clear error.
        import sys
        sys.stderr.write(
            "weasel: interactive mode requires chimera.weasel.repl "
            "(provided by W1).\n",
        )
        return 1
    # W1 exposes the REPL entry point as ``run`` (preferred) but we honour
    # the legacy ``run_repl`` alias if a future revision renames it.
    fn = getattr(repl, "run", None) or getattr(repl, "run_repl", None)
    if fn is None:
        import sys
        sys.stderr.write(
            "weasel: chimera.weasel.repl has no run/run_repl entry point.\n",
        )
        return 1
    return int(fn(args) or 0)


def _run_print(args: Any) -> int:
    """Lazy import + delegate to the one-shot print runner.

    The print runner lives in ``chimera.weasel.cli`` (W1). When unavailable
    we fall back to a tiny stdlib-only emitter that calls the SDK directly,
    so tests and integrators never see an ImportError mid-pipe.

    Args:
        args: Parsed CLI args; expected to carry ``prompt`` and ``json``.

    Returns:
        Exit code.
    """
    try:
        from chimera.weasel import cli as _cli  # type: ignore[attr-defined]
    except ImportError:
        _cli = None  # type: ignore[assignment]

    # W1's print runner is ``_run_print_mode``; allow the public alias
    # ``run_print`` too in case a future revision renames it.
    fn: Callable[..., int] | None = None
    if _cli is not None:
        fn = (
            getattr(_cli, "run_print", None)
            or getattr(_cli, "_run_print_mode", None)
        )
    if fn is not None:
        return int(fn(args) or 0)

    # Fallback: emit a minimal one-shot envelope so RPC/print pipelines
    # still terminate cleanly even before W1 lands.
    import json
    import sys

    prompt = getattr(args, "prompt", None) or ""
    error_msg = "weasel print runner not yet wired"
    payload: dict[str, Any] = {
        "prompt": prompt,
        "output": "",
        "success": False,
        "error": error_msg,
    }
    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(payload) + "\n")
    else:
        sys.stderr.write(error_msg + "\n")
    return 1


def _run_rpc(args: Any) -> int:
    """Run the JSON-RPC 2.0 stdio server.

    Args:
        args: Parsed CLI args.

    Returns:
        Exit code from :func:`chimera.weasel.rpc.run_rpc_server`.
    """
    from chimera.weasel.rpc import run_rpc_server
    return run_rpc_server(args)


def _run_sdk(args: Any) -> int:
    """Placeholder runner for the SDK mode.

    The SDK is normally consumed via ``from chimera.weasel.sdk import Agent``
    rather than the CLI; selecting ``--mode sdk`` from the command line is a
    no-op that prints a hint.

    Args:
        args: Parsed CLI args (unused).

    Returns:
        Exit code 0 after printing a hint.
    """
    import sys
    sys.stderr.write(
        "weasel: --mode sdk is reserved; import "
        "`from chimera.weasel.sdk import Agent` to use the SDK.\n",
    )
    return 0


_RUNNERS: dict[WeaselMode, Callable[[Any], int]] = {
    WeaselMode.INTERACTIVE: _run_interactive,
    WeaselMode.PRINT: _run_print,
    WeaselMode.RPC: _run_rpc,
    WeaselMode.SDK: _run_sdk,
}


__all__ = ["WeaselMode", "dispatch_mode"]
