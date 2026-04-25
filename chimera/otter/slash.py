"""Otter slash-command palette.

This module defines the otter REPL's slash-command set, mirroring the
upstream open-source coding agent's TUI command palette while reusing
Chimera's shared :mod:`chimera.cli.slash_commands` infrastructure for
the canonical handlers (``/help``, ``/model``, ``/cost``, ...).

The palette:

* Includes every command that the upstream agent exposes via its
  command dialog: ``/help``, ``/exit`` (+ ``/quit``), ``/share``,
  ``/agent`` (+ ``/agents``), ``/model`` (+ ``/models``), ``/init``,
  ``/sessions`` (+ ``/new``, ``/clear``), ``/cost``, ``/tools``,
  ``/undo``, ``/redo``, ``/edit``, ``/yolo``, ``/themes``,
  ``/status``, ``/mcps``, ``/connect``.
* Reuses the shared registry's handler whenever Chimera already has a
  near-equivalent (e.g. otter ``/agents`` -> shared ``cmd_agent``).
* Ships friendly placeholder stubs for commands whose backing
  subsystems are owned by sibling O-agents in the wave-1 build (share,
  sessions list, MCP toggles, theme switcher, edit/undo/redo). The
  stubs print ``not yet wired (owner: O<n>)`` rather than raising so a
  bare otter REPL is still useful end-to-end.

Two public surfaces are exposed:

* :data:`OTTER_SLASH_COMMANDS` — ``{name: handler}`` dict that the
  prompt-spec asks for; mirrors the wider Chimera slash-command
  contract (``handler(session, env, args, out)``).
* :data:`COMMANDS` — same dict, under the alias the otter REPL wires
  in :mod:`chimera.otter.repl` when merging overrides into the shared
  registry.

And one installer:

* :func:`register_otter_slash` — install every command on a REPL state
  object that exposes a ``register(name, handler, help_text)`` method
  (the shape of :mod:`chimera.cli.slash_commands`). When the state
  object lacks ``register``, we fall back to writing into a
  ``commands`` / ``slash_commands`` mapping, then to settattr — so the
  helper composes with both the shared registry and tiny test fakes.

Trademark hygiene: this module deliberately uses neutral phrasing
("the upstream coding agent") in any user-visible string, per
``research/otter/SPEC.md``.
"""
from __future__ import annotations

from typing import Any, Callable

__all__ = [
    "COMMANDS",
    "OTTER_SLASH_COMMANDS",
    "OTTER_SLASH_HELP",
    "PrintFn",
    "SlashHandler",
    "register_otter_slash",
]


PrintFn = Callable[[str], None]
SlashHandler = Callable[[Any, Any, str, PrintFn], None]


# ---------------------------------------------------------------------------
# Shared-registry passthroughs
# ---------------------------------------------------------------------------
#
# Most otter commands map directly onto handlers Chimera already ships in
# :mod:`chimera.cli.slash_commands`. We re-export those handlers (rather than
# re-implementing them) so behaviour stays in lockstep with ``chimera code``
# and ``chimera mink``. Imports are eager because the shared registry has no
# optional deps — and a circular import is not possible here (the shared
# module never reaches into ``chimera.otter``).

from chimera.cli.slash_commands import (  # noqa: E402 -- intentional after docstring
    cmd_compact as _cmd_compact,
    cmd_config as _cmd_config,
    cmd_cost as _cmd_cost,
    cmd_doctor as _cmd_doctor,
    cmd_help as _cmd_help,
    cmd_mcp as _cmd_mcp,
    cmd_status as _cmd_status,
)

# ``cmd_agent``, ``cmd_clear``, ``cmd_exit``, ``cmd_init``, ``cmd_model``,
# ``cmd_session``, ``cmd_tools``, ``cmd_yolo`` live in :mod:`chimera.cli.code`.
# They follow the same ``(session, env, args, out)`` signature.
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
# Otter-flavored placeholder stubs (owned by sibling O-agents)
# ---------------------------------------------------------------------------

def _stub(message: str) -> SlashHandler:
    """Build a stub handler that prints ``message`` and returns.

    Stubs are used for commands whose backing subsystems are owned by
    other O-agents in the wave-1 build. Once the sibling module lands
    (e.g. O13 ships share, O3 ships ``sessions list``), the stub is
    swapped out for a real handler in a follow-up patch.
    """

    def _handler(_session: Any, _env: Any, _args: str, out: PrintFn) -> None:
        out(message)

    return _handler


def cmd_share(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """Share the current session via the otter share command (O13).

    Late-binds :mod:`chimera.otter.share_cmd` so this REPL command works
    once O13 lands without touching the registry. Falls back to a stub
    message until the share module exists.
    """
    try:
        from chimera.otter import share_cmd as _share  # type: ignore[attr-defined]
    except ImportError:
        out("not yet wired: /share will be available once the share subcommand lands (owner: O13)")
        return

    runner = getattr(_share, "share_session", None) or getattr(_share, "run", None)
    if runner is None:
        out("not yet wired: /share handler missing (owner: O13)")
        return
    try:
        runner(session=session, env=env, args=args, out=out)
    except Exception as exc:  # noqa: BLE001 -- surface, never crash REPL
        out(f"share failed: {exc}")


def cmd_sessions(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """List or switch sessions via the otter sessions command (O3).

    Late-binds :mod:`chimera.otter.sessions` so the listing surface
    stays in sync with whatever O3 ships. Falls back to the shared
    ``cmd_session`` (save/list/fork) when the otter-specific module
    doesn't expose a slash entry point yet.
    """
    try:
        from chimera.otter import sessions as _sessions  # type: ignore[attr-defined]
    except ImportError:
        _cmd_session(session, env, args, out)
        return

    handler = getattr(_sessions, "slash_handler", None)
    if handler is None:
        # O3 hasn't published a slash handler yet; fall back to the
        # shared session command so the user still has save/list/fork.
        _cmd_session(session, env, args, out)
        return
    try:
        handler(session, env, args, out)
    except Exception as exc:  # noqa: BLE001
        out(f"sessions failed: {exc}")


def cmd_new(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """Start a new session: clear the current context.

    The upstream agent treats ``/new`` and ``/clear`` as aliases (both
    reset the live conversation). We honor that by delegating to the
    shared ``cmd_clear`` handler.
    """
    _cmd_clear(session, env, args, out)


def cmd_undo(_session: Any, _env: Any, _args: str, out: PrintFn) -> None:
    """Undo the last assistant turn (placeholder; owner: O2/O3).

    The shared REPL doesn't currently track per-turn snapshots, so the
    undo verb degrades to a friendly notice. Once the session-tree work
    grows a turn-level rewind, we'll route through it here.
    """
    out("not yet wired: /undo will be available once turn-level rewind lands (owner: O2)")


def cmd_redo(_session: Any, _env: Any, _args: str, out: PrintFn) -> None:
    """Redo a turn previously rewound by ``/undo`` (placeholder)."""
    out("not yet wired: /redo will be available once turn-level rewind lands (owner: O2)")


def cmd_edit(_session: Any, _env: Any, _args: str, out: PrintFn) -> None:
    """Open an external ``$EDITOR`` for the next prompt (placeholder)."""
    out("not yet wired: /edit will open $EDITOR for the next prompt (owner: O2)")


def cmd_themes(_session: Any, _env: Any, _args: str, out: PrintFn) -> None:
    """Switch the REPL theme (placeholder; owner: O2 / docs).

    The shared REPL is currently theme-less; this stub keeps the
    command discoverable so users coming from the upstream agent see a
    consistent palette.
    """
    out("not yet wired: /themes will be available once the REPL grows a theme switcher (owner: O2)")


def cmd_connect(_session: Any, _env: Any, args: str, out: PrintFn) -> None:
    """Connect a provider via the providers helper (O12).

    The upstream agent uses ``/connect`` to launch its provider-list
    dialog. We late-bind :mod:`chimera.otter.providers` so this hook
    lights up automatically once O12 lands, and prints a hint
    otherwise.
    """
    try:
        from chimera.otter import providers as _providers  # type: ignore[attr-defined]
    except ImportError:
        out("not yet wired: /connect will be available once provider wiring lands (owner: O12)")
        return

    handler = (
        getattr(_providers, "slash_connect", None)
        or getattr(_providers, "connect", None)
    )
    if handler is None:
        out("not yet wired: /connect handler missing (owner: O12)")
        return
    try:
        handler(args=args, out=out)
    except Exception as exc:  # noqa: BLE001
        out(f"connect failed: {exc}")


def cmd_mcps(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """List configured MCP servers (alias for the shared ``/mcp``).

    The upstream agent surfaces this under ``/mcps`` (plural). We mirror
    that by aliasing onto the shared ``cmd_mcp`` so existing config
    discovery (project ``.mcp.json`` -> user ``~/.chimera/mcp.json``)
    still works through either spelling.
    """
    _cmd_mcp(session, env, args, out)


def cmd_models(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """List or cycle models (alias for the shared ``/model``)."""
    _cmd_model(session, env, args, out)


def cmd_agents(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """List agent presets (alias for the shared ``/agent``)."""
    _cmd_agent(session, env, args, out)


def cmd_quit(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """Leave the REPL (alias for ``/exit``)."""
    _cmd_exit(session, env, args, out)


# ---------------------------------------------------------------------------
# The palette
# ---------------------------------------------------------------------------
#
# Maps ``name -> handler``; help text lives in :data:`OTTER_SLASH_HELP` so
# :func:`register_otter_slash` can register both pieces against the shared
# registry. Order matches the upstream command dialog's grouping (Session ->
# Agent -> Provider -> System -> Prompt) for review-friendliness.

OTTER_SLASH_COMMANDS: dict[str, SlashHandler] = {
    # Session
    "sessions": cmd_sessions,
    "new": cmd_new,
    "clear": _cmd_clear,
    "share": cmd_share,
    "undo": cmd_undo,
    "redo": cmd_redo,
    # Agent
    "agent": _cmd_agent,
    "agents": cmd_agents,
    "model": _cmd_model,
    "models": cmd_models,
    "tools": _cmd_tools,
    "yolo": _cmd_yolo,
    # Provider
    "connect": cmd_connect,
    "mcp": _cmd_mcp,
    "mcps": cmd_mcps,
    # System
    "help": _cmd_help,
    "status": _cmd_status,
    "doctor": _cmd_doctor,
    "config": _cmd_config,
    "cost": _cmd_cost,
    "compact": _cmd_compact,
    "init": _cmd_init,
    "themes": cmd_themes,
    "exit": _cmd_exit,
    "quit": cmd_quit,
    # Prompt
    "edit": cmd_edit,
}

# Alias used by :mod:`chimera.otter.repl._resolve_slash_registry`. Keep the
# two names in lockstep — exposing both lets the REPL pick up overrides via
# its existing contract while still satisfying callers that ask for the
# explicit ``OTTER_SLASH_COMMANDS`` symbol.
COMMANDS: dict[str, SlashHandler] = OTTER_SLASH_COMMANDS


OTTER_SLASH_HELP: dict[str, str] = {
    # Session
    "sessions": "list or switch sessions",
    "new": "start a new session (clears context)",
    "clear": "clear the current context",
    "share": "share the current session",
    "undo": "undo the last turn (coming soon)",
    "redo": "redo a previously undone turn (coming soon)",
    # Agent
    "agent": "list agent presets",
    "agents": "list agent presets",
    "model": "show or cycle the active model",
    "models": "show or cycle the active model",
    "tools": "list available tools",
    "yolo": "toggle auto-approve mode",
    # Provider
    "connect": "connect a provider",
    "mcp": "list MCP servers and tools",
    "mcps": "list MCP servers and tools",
    # System
    "help": "show this list",
    "status": "one-screen status summary",
    "doctor": "environment health checks",
    "config": "print effective merged settings",
    "cost": "show cumulative cost",
    "compact": "force a HARD threshold compaction now",
    "init": "summarise the project",
    "themes": "switch the REPL theme (coming soon)",
    "exit": "leave the REPL",
    "quit": "leave the REPL",
    # Prompt
    "edit": "open $EDITOR for the next prompt (coming soon)",
}


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------

def register_otter_slash(repl_state: Any) -> int:
    """Install every otter slash command onto ``repl_state``.

    This composes with three flavors of REPL state, in priority order:

    1. The shared :mod:`chimera.cli.slash_commands` module itself, or
       any object exposing ``register(name, handler, help_text)``.
    2. A state object exposing a ``commands`` or ``slash_commands``
       mapping (for ad-hoc REPL fakes used in tests).
    3. Anything else: we ``setattr(repl_state, name, handler)`` so the
       commands at least become discoverable as attributes.

    The function never raises on a missing handler; missing surfaces
    are silently skipped so a partially-built REPL state still works.

    Args:
        repl_state: Target onto which the otter palette is installed.

    Returns:
        The count of commands successfully installed.
    """
    register = getattr(repl_state, "register", None)
    if callable(register):
        installed = 0
        for name, handler in OTTER_SLASH_COMMANDS.items():
            help_text = OTTER_SLASH_HELP.get(name, "")
            try:
                register(name, handler, help_text)
            except TypeError:
                # Older signatures may not accept ``help_text`` -- retry
                # with the handler-only form so we still install.
                try:
                    register(name, handler)
                except Exception:  # noqa: BLE001 -- best-effort install
                    continue
            installed += 1
        return installed

    # ``commands`` / ``slash_commands`` dict-style states
    for attr in ("commands", "slash_commands"):
        bag = getattr(repl_state, attr, None)
        if isinstance(bag, dict):
            for name, handler in OTTER_SLASH_COMMANDS.items():
                bag[name] = handler
            return len(OTTER_SLASH_COMMANDS)

    # Last resort: write each command on as an attribute so callers can
    # find them by name even on a bare object.
    for name, handler in OTTER_SLASH_COMMANDS.items():
        try:
            setattr(repl_state, name, handler)
        except (AttributeError, TypeError):
            continue
    return len(OTTER_SLASH_COMMANDS)
