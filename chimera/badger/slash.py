"""Badger slash-command palette.

Mirrors the upstream's command palette while reusing Chimera's shared
:mod:`chimera.cli.slash_commands` infrastructure for the canonical
handlers. Adds the badger-specific commands ``/parity`` and ``/rerun``
which expose the harness-rewrite knobs from inside the REPL.

Public surface:

* :data:`BADGER_SLASH_COMMANDS` — ``{name: handler}`` dict.
* :data:`BADGER_SLASH_HELP` — ``{name: help_text}`` dict.
* :func:`register_badger_slash` — install the palette onto a REPL state.

Trademark hygiene: comparative language uses neutral phrasing — the
upstream is referenced as "the upstream" or not at all.
"""

from __future__ import annotations

from typing import Any, Callable

__all__ = [
    "BADGER_SLASH_COMMANDS",
    "BADGER_SLASH_HELP",
    "PrintFn",
    "SlashHandler",
    "cmd_parity",
    "cmd_rerun",
    "register_badger_slash",
]


PrintFn = Callable[[str], None]
SlashHandler = Callable[[Any, Any, str, PrintFn], None]


# ---------------------------------------------------------------------------
# Shared handlers — pulled from chimera.cli.slash_commands and chimera.cli.code.
# ---------------------------------------------------------------------------

from chimera.cli.slash_commands import (  # noqa: E402
    cmd_compact as _cmd_compact,
    cmd_config as _cmd_config,
    cmd_cost as _cmd_cost,
    cmd_doctor as _cmd_doctor,
    cmd_help as _cmd_help,
    cmd_status as _cmd_status,
)

from chimera.cli.code import (  # noqa: E402
    cmd_agent as _cmd_agent,
    cmd_clear as _cmd_clear,
    cmd_exit as _cmd_exit,
    cmd_init as _cmd_init,
    cmd_model as _cmd_model,
    cmd_session as _cmd_session,
    cmd_tools as _cmd_tools,
    cmd_yolo as _cmd_yolo,
)


# ---------------------------------------------------------------------------
# Badger-specific commands
# ---------------------------------------------------------------------------


def cmd_parity(_session: Any, _env: Any, args: str, out: PrintFn) -> None:
    """Run a parity check against a schema file from inside the REPL.

    Usage:
        ``/parity`` — auto-resolve schema (PARITY.md / PARITY.json in cwd).
        ``/parity <path>`` — load the schema at *path*.

    Late-binds :mod:`chimera.badger.parity` so the slash registry is
    importable even on a partial install.
    """
    try:
        from chimera.badger.parity import (
            build_live_snapshot,
            diff_schema,
            format_report,
            load_schema,
        )
    except ImportError as exc:
        out(f"/parity: parity module unavailable ({exc})")
        return

    from pathlib import Path

    raw = args.strip()
    if raw:
        path = Path(raw)
    else:
        cwd = Path.cwd()
        for name in ("PARITY.md", "PARITY.json", "PARITY.yaml", "PARITY.yml"):
            candidate = cwd / name
            if candidate.exists():
                path = candidate
                break
        else:
            out("/parity: no schema found in cwd. Pass /parity <path>.")
            return
    try:
        expected = load_schema(path)
    except Exception as exc:  # noqa: BLE001
        out(f"/parity: load failed: {exc}")
        return
    live = build_live_snapshot()
    report = diff_schema(expected, live)
    out(format_report(report))


def cmd_rerun(session: Any, _env: Any, args: str, out: PrintFn) -> None:
    """Show or set the rerun-on-failure budget for this session.

    Usage:
        ``/rerun`` — print the current budget.
        ``/rerun on`` / ``/rerun off`` — enable / disable rerun.
        ``/rerun <int>`` — set ``max_reruns`` (also enables rerun).

    The session's state lives at ``session.rerun_on_failure`` (bool) and
    ``session.max_reruns`` (int). Missing attributes default to off / 2.
    """
    current_on = bool(getattr(session, "rerun_on_failure", False))
    current_n = int(getattr(session, "max_reruns", 2) or 2)
    raw = args.strip().lower()

    if not raw:
        out(f"/rerun: rerun_on_failure={current_on} max_reruns={current_n}")
        return
    if raw in ("on", "true", "1", "yes"):
        try:
            setattr(session, "rerun_on_failure", True)
        except (AttributeError, TypeError):
            out("/rerun: cannot persist on session")
            return
        out(f"/rerun: enabled (max_reruns={current_n})")
        return
    if raw in ("off", "false", "0", "no"):
        try:
            setattr(session, "rerun_on_failure", False)
        except (AttributeError, TypeError):
            out("/rerun: cannot persist on session")
            return
        out("/rerun: disabled")
        return
    try:
        n = int(raw)
    except ValueError:
        out(f"/rerun: unrecognized argument {args.strip()!r} (use on/off/<int>)")
        return
    if n < 0:
        out("/rerun: max_reruns must be >= 0")
        return
    try:
        setattr(session, "rerun_on_failure", True)
        setattr(session, "max_reruns", n)
    except (AttributeError, TypeError):
        out("/rerun: cannot persist on session")
        return
    out(f"/rerun: enabled, max_reruns={n}")


# ---------------------------------------------------------------------------
# The palette
# ---------------------------------------------------------------------------

BADGER_SLASH_COMMANDS: dict[str, SlashHandler] = {
    # Session
    "clear": _cmd_clear,
    "session": _cmd_session,
    # Agent
    "agent": _cmd_agent,
    "model": _cmd_model,
    "tools": _cmd_tools,
    "yolo": _cmd_yolo,
    # Badger-specific (harness-rewrite posture)
    "parity": cmd_parity,
    "rerun": cmd_rerun,
    # System
    "help": _cmd_help,
    "status": _cmd_status,
    "doctor": _cmd_doctor,
    "config": _cmd_config,
    "cost": _cmd_cost,
    "compact": _cmd_compact,
    "init": _cmd_init,
    "exit": _cmd_exit,
    "quit": _cmd_exit,
}


BADGER_SLASH_HELP: dict[str, str] = {
    "clear": "clear the current context",
    "session": "save / list / fork the current session",
    "agent": "list agent presets",
    "model": "show or cycle the active model",
    "tools": "list available tools",
    "yolo": "toggle auto-approve mode",
    "parity": "run a parity check against a schema (e.g. PARITY.md)",
    "rerun": "show / set the rerun-on-failure budget for this session",
    "help": "show this list",
    "status": "one-screen status summary",
    "doctor": "environment health checks",
    "config": "print effective merged settings",
    "cost": "show cumulative cost",
    "compact": "force a HARD threshold compaction now",
    "init": "summarise the project",
    "exit": "leave the REPL",
    "quit": "leave the REPL",
}


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------


def _install_one(
    repl_state: Any, name: str, handler: SlashHandler, help_text: str,
) -> bool:
    """Install a single ``(name, handler, help_text)`` triple onto *repl_state*."""
    register = getattr(repl_state, "register", None)
    if callable(register):
        try:
            register(name, handler, help_text)
            return True
        except TypeError:
            try:
                register(name, handler)
                return True
            except Exception:  # noqa: BLE001
                return False

    for attr in ("commands", "slash_commands"):
        bag = getattr(repl_state, attr, None)
        if isinstance(bag, dict):
            bag[name] = handler
            return True

    try:
        setattr(repl_state, name, handler)
        return True
    except (AttributeError, TypeError):
        return False


def register_badger_slash(repl_state: Any) -> int:
    """Install every badger slash command onto ``repl_state``.

    Args:
        repl_state: Target object onto which the badger palette is
            installed. Accepts a ``register(name, handler, help_text)``
            method, or a ``commands`` / ``slash_commands`` mapping, or
            a plain object (we ``setattr`` as a last resort).

    Returns:
        The count of commands successfully installed.
    """
    installed = 0
    for name, handler in BADGER_SLASH_COMMANDS.items():
        help_text = BADGER_SLASH_HELP.get(name, "")
        if _install_one(repl_state, name, handler, help_text):
            installed += 1
    return installed
