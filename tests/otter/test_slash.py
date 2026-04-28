"""Tests for the otter slash-command palette.

The palette is the otter-flavored equivalent of the upstream open-source
coding agent's TUI command dialog. We verify three contracts:

1. **Coverage** — every command name the upstream agent exposes via
   ``slash:`` (sessions, new, models, agents, mcps, themes, status,
   help, exit/quit, share, undo, redo, edit, yolo, connect) is present
   in the otter palette, plus we ship at least 12 commands total.
2. **Schema** — every command maps to a callable handler with the
   ``(session, env, args, out)`` shape, and every command has a help
   string in :data:`OTTER_SLASH_HELP`.
3. **Behaviour** — placeholder stubs (`/share`, `/themes`, `/edit`)
   print friendly "not yet wired" messages instead of raising. `/undo`
   and `/redo` are wired to a per-session checkpoint stack (see
   :mod:`tests.otter.test_slash_undo_redo`). `/help` lists registered
   commands when called against the shared registry.
"""
from __future__ import annotations

import inspect
from typing import Any

from chimera.otter.slash import (
    COMMANDS,
    OTTER_SLASH_COMMANDS,
    OTTER_SLASH_HELP,
    cmd_edit,
    cmd_new,
    cmd_share,
    cmd_themes,
    cmd_undo,
    register_otter_slash,
)


# ---------------------------------------------------------------------------
# Coverage / schema
# ---------------------------------------------------------------------------

# The set of upstream slash names lifted from the upstream agent's TUI
# command dialog. Kept inline here so the test fails loudly if otter
# silently drops a command (regression guard for the parity matrix).
EXPECTED_NAMES = {
    "sessions",
    "new",
    "clear",
    "share",
    "undo",
    "redo",
    "agent",
    "agents",
    "model",
    "models",
    "tools",
    "yolo",
    "connect",
    "mcp",
    "mcps",
    "help",
    "status",
    "doctor",
    "config",
    "cost",
    "compact",
    "init",
    "themes",
    "exit",
    "quit",
    "edit",
}


def test_palette_has_at_least_twelve_commands() -> None:
    """Wave-1 floor: at least 12 commands implemented or stubbed."""
    assert len(OTTER_SLASH_COMMANDS) >= 12


def test_expected_names_are_all_present() -> None:
    """Every upstream-equivalent name resolves to a handler."""
    missing = sorted(EXPECTED_NAMES - set(OTTER_SLASH_COMMANDS))
    assert not missing, f"otter palette is missing: {missing}"


def test_commands_alias_points_at_same_dict() -> None:
    """``COMMANDS`` is the alias the otter REPL imports; keep them in sync."""
    assert COMMANDS is OTTER_SLASH_COMMANDS


def test_every_command_has_callable_handler() -> None:
    """Every entry must be callable so the registry can dispatch it."""
    for name, handler in OTTER_SLASH_COMMANDS.items():
        assert callable(handler), f"/{name} handler is not callable: {handler!r}"


def test_every_command_has_help_text() -> None:
    """Every command has a non-empty help string for the registry."""
    for name in OTTER_SLASH_COMMANDS:
        assert name in OTTER_SLASH_HELP, f"/{name} missing from OTTER_SLASH_HELP"
        assert OTTER_SLASH_HELP[name].strip(), f"/{name} has empty help text"


def test_handler_signatures_match_registry_contract() -> None:
    """Handlers must accept ``(session, env, args, out)``.

    The shared :mod:`chimera.cli.slash_commands` registry dispatches by
    calling ``handler(session, env, args, out)``; if the otter palette
    drifted off that signature, dispatch would crash at runtime. We
    inspect each callable signature here so the failure is loud and
    early.
    """
    for name, handler in OTTER_SLASH_COMMANDS.items():
        try:
            sig = inspect.signature(handler)
        except (TypeError, ValueError):
            # Builtins or C functions — accept by exclusion.
            continue
        positional = [
            p for p in sig.parameters.values()
            if p.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        assert len(positional) >= 4, (
            f"/{name} handler must accept (session, env, args, out); "
            f"got {sig}"
        )


# ---------------------------------------------------------------------------
# Behaviour smoke tests (4 commands)
# ---------------------------------------------------------------------------

class _CapturePrinter:
    """Tiny callable that records each line printed by a handler."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str = "") -> None:
        self.lines.append(line)


class _FakeSession:
    """Minimal duck-typed session sufficient for stub handlers.

    The real :class:`chimera.sessions.session.Session` carries a fully
    configured agent + provider + context, but the four handlers we
    smoke-test here either return ``not yet wired`` or only inspect
    ``session.context`` for an empty case. We give them both surfaces
    so they degrade cleanly.
    """

    def __init__(self) -> None:
        self.context = None
        self.provider = None
        self.cost_tracker = None


def test_share_prints_not_yet_wired_message() -> None:
    """/share is a stub until O13 lands; verify the friendly fallback."""
    out = _CapturePrinter()
    cmd_share(_FakeSession(), None, "", out)
    text = "\n".join(out.lines)
    assert "not yet wired" in text
    assert "O13" in text


def test_undo_with_empty_stack_prints_friendly_notice() -> None:
    """/undo with no prior snapshots reports nothing to undo (not a crash)."""
    out = _CapturePrinter()
    cmd_undo(_FakeSession(), None, "", out)
    text = "\n".join(out.lines)
    assert "/undo" in text
    assert "nothing to undo" in text


def test_themes_prints_not_yet_wired_message() -> None:
    """/themes degrades to a friendly notice on a theme-less REPL."""
    out = _CapturePrinter()
    cmd_themes(_FakeSession(), None, "", out)
    text = "\n".join(out.lines)
    assert "not yet wired" in text
    assert "/themes" in text


def test_edit_prints_not_yet_wired_message() -> None:
    """/edit will eventually invoke $EDITOR; for now it advertises that."""
    out = _CapturePrinter()
    cmd_edit(_FakeSession(), None, "", out)
    text = "\n".join(out.lines)
    assert "not yet wired" in text
    assert "$EDITOR" in text


def test_new_clears_via_shared_handler() -> None:
    """/new aliases /clear; the shared handler should run without error.

    The shared :func:`chimera.cli.code.cmd_clear` calls ``session.clear()``
    directly, so we attach a counter to verify it ran rather than asserting
    on the (often empty) print output.
    """

    class _ClearableSession(_FakeSession):
        def __init__(self) -> None:
            super().__init__()
            self.clear_count = 0

        def clear(self) -> None:
            self.clear_count += 1

    out = _CapturePrinter()
    session = _ClearableSession()
    cmd_new(session, None, "", out)
    assert session.clear_count == 1
    assert any("cleared" in line.lower() for line in out.lines)


# ---------------------------------------------------------------------------
# register_otter_slash
# ---------------------------------------------------------------------------

class _FakeRegistryState:
    """Mimics the shared :mod:`chimera.cli.slash_commands` register API."""

    def __init__(self) -> None:
        self.entries: dict[str, tuple[Any, str]] = {}

    def register(self, name: str, handler: Any, help_text: str = "") -> None:
        self.entries[name] = (handler, help_text)


class _FakeDictState:
    """REPL state that exposes commands as a plain dict (test fake)."""

    def __init__(self) -> None:
        self.commands: dict[str, Any] = {}


def test_register_otter_slash_uses_register_method() -> None:
    """When the state has ``register``, the installer routes through it."""
    state = _FakeRegistryState()
    n = register_otter_slash(state)
    assert n == len(OTTER_SLASH_COMMANDS)
    # Every command landed with its help text (non-empty for all entries).
    for name in OTTER_SLASH_COMMANDS:
        assert name in state.entries
        handler, help_text = state.entries[name]
        assert callable(handler)
        assert help_text == OTTER_SLASH_HELP[name]


def test_register_otter_slash_falls_back_to_dict_attribute() -> None:
    """A state with a ``commands`` dict should be populated in place."""
    state = _FakeDictState()
    n = register_otter_slash(state)
    assert n == len(OTTER_SLASH_COMMANDS)
    for name, handler in OTTER_SLASH_COMMANDS.items():
        assert state.commands[name] is handler


def test_register_otter_slash_handles_register_without_help_arg() -> None:
    """Older registries may only accept ``(name, handler)``."""

    class _OldRegistry:
        def __init__(self) -> None:
            self.entries: dict[str, Any] = {}

        # Two-arg signature — passing a third arg would raise TypeError.
        def register(self, name: str, handler: Any) -> None:
            self.entries[name] = handler

    state = _OldRegistry()
    n = register_otter_slash(state)
    assert n == len(OTTER_SLASH_COMMANDS)
    for name, handler in OTTER_SLASH_COMMANDS.items():
        assert state.entries[name] is handler


def test_register_otter_slash_against_shared_registry_lists_help() -> None:
    """End-to-end smoke: install onto the real shared registry, then /help.

    This pins the contract in :mod:`chimera.otter.repl._resolve_slash_registry`
    -> :func:`chimera.cli.slash_commands.register`. We don't dispatch a
    real prompt; we just confirm the names show up in ``list_commands``
    and ``cmd_help`` prints them.
    """
    from chimera.cli import slash_commands as shared

    register_otter_slash(shared)
    names = {name for name, _ in shared.list_commands()}
    # A handful of otter-only entries should now be present alongside
    # the shared defaults.
    for must_have in ("sessions", "new", "share", "themes", "edit"):
        assert must_have in names

    out = _CapturePrinter()
    shared.cmd_help(None, None, "", out)
    rendered = "\n".join(out.lines)
    assert "/sessions" in rendered
    assert "/share" in rendered
