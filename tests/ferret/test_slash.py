"""Tests for the ferret slash-command palette.

The palette is the ferret-flavored equivalent of the upstream
sandbox-first / IDE-first coding agent's command dialog. We verify
four contracts:

1. **Coverage** — every command name the spec calls out (sessions,
   new, models, agents, help, exit/quit, share, undo, redo, yolo,
   *plus* the ferret-specific ``/sandbox`` / ``/approval`` / ``/diff``
   trio) is present in the palette, plus we ship at least 12 commands
   total.
2. **Schema** — every command maps to a callable handler with the
   ``(session, env, args, out)`` shape, and every command has a help
   string in :data:`FERRET_SLASH_HELP`.
3. **Behaviour** — placeholder stubs (`/share`) print friendly
   ``not yet wired`` messages instead of raising. ``/undo`` returns a
   friendly notice on an empty stack. ``/sandbox`` and ``/approval``
   cycle, accept an explicit value, and reject unknown values. ``/diff``
   reads from the session's :class:`FileTracker` when present and
   degrades to "no pending changes" otherwise.
4. **Installer** — :func:`register_ferret_slash` composes with the
   shared registry, dict-style fakes, and old-style two-arg ``register``.
"""
from __future__ import annotations

import inspect
from typing import Any

from chimera.ferret.slash import (
    APPROVAL_PRESETS,
    COMMANDS,
    FERRET_SLASH_COMMANDS,
    FERRET_SLASH_HELP,
    SANDBOX_MODES,
    cmd_approval,
    cmd_diff,
    cmd_new,
    cmd_sandbox,
    cmd_share,
    cmd_undo,
    register_ferret_slash,
)


# ---------------------------------------------------------------------------
# Coverage / schema
# ---------------------------------------------------------------------------

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
    # Ferret-specific
    "sandbox",
    "approval",
    "diff",
    # System
    "help",
    "status",
    "doctor",
    "config",
    "cost",
    "compact",
    "init",
    "exit",
    "quit",
}


def test_palette_has_at_least_twelve_commands() -> None:
    """Wave-5 floor: at least 12 commands implemented or stubbed."""
    assert len(FERRET_SLASH_COMMANDS) >= 12


def test_expected_names_are_all_present() -> None:
    """Every spec-required name resolves to a handler."""
    missing = sorted(EXPECTED_NAMES - set(FERRET_SLASH_COMMANDS))
    assert not missing, f"ferret palette is missing: {missing}"


def test_ferret_specific_trio_present() -> None:
    """The ferret-only ``/sandbox`` / ``/approval`` / ``/diff`` trio."""
    for name in ("sandbox", "approval", "diff"):
        assert name in FERRET_SLASH_COMMANDS, f"/{name} missing"


def test_commands_alias_points_at_same_dict() -> None:
    """``COMMANDS`` is the alias the ferret REPL imports; keep them in sync."""
    assert COMMANDS is FERRET_SLASH_COMMANDS


def test_every_command_has_callable_handler() -> None:
    """Every entry must be callable so the registry can dispatch it."""
    for name, handler in FERRET_SLASH_COMMANDS.items():
        assert callable(handler), f"/{name} handler is not callable: {handler!r}"


def test_every_command_has_help_text() -> None:
    """Every command has a non-empty help string for the registry."""
    for name in FERRET_SLASH_COMMANDS:
        assert name in FERRET_SLASH_HELP, f"/{name} missing from FERRET_SLASH_HELP"
        assert FERRET_SLASH_HELP[name].strip(), f"/{name} has empty help text"


def test_handler_signatures_match_registry_contract() -> None:
    """Handlers must accept ``(session, env, args, out)``."""
    for name, handler in FERRET_SLASH_COMMANDS.items():
        try:
            sig = inspect.signature(handler)
        except (TypeError, ValueError):
            continue
        positional = [
            p for p in sig.parameters.values()
            if p.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        assert len(positional) >= 4, (
            f"/{name} handler must accept (session, env, args, out); got {sig}"
        )


# ---------------------------------------------------------------------------
# Behaviour smoke tests
# ---------------------------------------------------------------------------

class _CapturePrinter:
    """Tiny callable that records each line printed by a handler."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str = "") -> None:
        self.lines.append(line)


class _FakeSession:
    """Minimal duck-typed session sufficient for the ferret handlers.

    Carries the three attributes the ferret-specific handlers consult
    (``sandbox_mode``, ``approval_preset``, ``file_tracker``) plus the
    ``context`` slot used by the shared handlers.
    """

    def __init__(self) -> None:
        self.context = None
        self.provider = None
        self.cost_tracker = None
        self.sandbox_mode = "read-only"
        self.approval_preset = "read-only"
        self.file_tracker = None


def test_share_prints_not_yet_wired_message() -> None:
    """/share is a stub until its sibling F-agent lands."""
    out = _CapturePrinter()
    cmd_share(_FakeSession(), None, "", out)
    text = "\n".join(out.lines)
    assert "not yet wired" in text


def test_undo_with_empty_stack_prints_friendly_notice() -> None:
    """/undo with no prior snapshots reports nothing to undo (not a crash)."""
    out = _CapturePrinter()
    cmd_undo(_FakeSession(), None, "", out)
    text = "\n".join(out.lines)
    assert "/undo" in text
    assert "nothing to undo" in text


def test_new_clears_via_shared_handler() -> None:
    """/new aliases /clear; the shared handler should run without error."""

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
# Ferret-specific commands
# ---------------------------------------------------------------------------


def test_sandbox_cycles_through_modes() -> None:
    """Bare ``/sandbox`` advances through the documented triplet."""
    session = _FakeSession()
    out = _CapturePrinter()

    # Walk the full cycle.
    visited: list[str] = []
    for _ in range(len(SANDBOX_MODES) + 1):
        cmd_sandbox(session, None, "", out)
        visited.append(session.sandbox_mode)
    # First step lands at index 1; full cycle returns to index 1 again.
    assert visited[0] == SANDBOX_MODES[1]
    assert visited[-1] == visited[0]
    # Every documented mode must appear at some point.
    assert set(SANDBOX_MODES).issubset(set([SANDBOX_MODES[0], *visited]))


def test_sandbox_explicit_value_sets() -> None:
    """``/sandbox workspace-write-network`` jumps directly to that mode."""
    session = _FakeSession()
    out = _CapturePrinter()
    cmd_sandbox(session, None, "workspace-write-network", out)
    assert session.sandbox_mode == "workspace-write-network"


def test_sandbox_unknown_value_rejected() -> None:
    """An unknown explicit mode prints an error and leaves state alone."""
    session = _FakeSession()
    session.sandbox_mode = "read-only"
    out = _CapturePrinter()
    cmd_sandbox(session, None, "loose", out)
    assert session.sandbox_mode == "read-only"
    text = "\n".join(out.lines)
    assert "unknown mode" in text


def test_approval_cycles_through_presets() -> None:
    """Bare ``/approval`` advances through the documented triplet."""
    session = _FakeSession()
    out = _CapturePrinter()

    visited: list[str] = []
    for _ in range(len(APPROVAL_PRESETS) + 1):
        cmd_approval(session, None, "", out)
        visited.append(session.approval_preset)
    assert visited[0] == APPROVAL_PRESETS[1]
    assert visited[-1] == visited[0]


def test_approval_explicit_value_sets() -> None:
    """``/approval full`` lands on the full preset."""
    session = _FakeSession()
    out = _CapturePrinter()
    cmd_approval(session, None, "full", out)
    assert session.approval_preset == "full"


def test_approval_unknown_value_rejected() -> None:
    """An unknown explicit preset prints an error and leaves state alone."""
    session = _FakeSession()
    session.approval_preset = "read-only"
    out = _CapturePrinter()
    cmd_approval(session, None, "yolo-mode", out)
    assert session.approval_preset == "read-only"
    text = "\n".join(out.lines)
    assert "unknown preset" in text


def test_diff_with_no_tracker_prints_no_changes() -> None:
    """/diff with no file tracker and no env hook reports cleanly."""
    out = _CapturePrinter()
    cmd_diff(_FakeSession(), None, "", out)
    text = "\n".join(out.lines)
    assert "/diff" in text
    assert "no pending changes" in text


def test_diff_lists_modified_and_read_files_from_tracker() -> None:
    """When ``session.file_tracker`` is populated, /diff lists both buckets."""
    from chimera.core.file_tracker import FileTracker

    session = _FakeSession()
    tracker = FileTracker()
    tracker.record_modified("a.py")
    tracker.record_modified("b.py")
    tracker.record_read("c.md")
    session.file_tracker = tracker

    out = _CapturePrinter()
    cmd_diff(session, None, "", out)
    text = "\n".join(out.lines)
    assert "Pending file activity" in text
    assert "modified (2)" in text
    assert "a.py" in text and "b.py" in text
    assert "read     (1)" in text
    assert "c.md" in text


def test_diff_falls_back_to_env_hook() -> None:
    """When the tracker is empty but env exposes a diff hook, use it."""

    class _EnvWithDiff:
        def pending_diff(self) -> str:
            return "diff --git a/x b/x\n+hello\n"

    out = _CapturePrinter()
    cmd_diff(_FakeSession(), _EnvWithDiff(), "", out)
    text = "\n".join(out.lines)
    assert "diff --git" in text


# ---------------------------------------------------------------------------
# register_ferret_slash
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


def test_register_ferret_slash_uses_register_method() -> None:
    """When the state has ``register``, the installer routes through it."""
    state = _FakeRegistryState()
    n = register_ferret_slash(state)
    assert n == len(FERRET_SLASH_COMMANDS)
    for name in FERRET_SLASH_COMMANDS:
        assert name in state.entries
        handler, help_text = state.entries[name]
        assert callable(handler)
        assert help_text == FERRET_SLASH_HELP[name]


def test_register_ferret_slash_falls_back_to_dict_attribute() -> None:
    """A state with a ``commands`` dict should be populated in place."""
    state = _FakeDictState()
    n = register_ferret_slash(state)
    assert n == len(FERRET_SLASH_COMMANDS)
    for name, handler in FERRET_SLASH_COMMANDS.items():
        assert state.commands[name] is handler


def test_register_ferret_slash_handles_register_without_help_arg() -> None:
    """Older registries may only accept ``(name, handler)``."""

    class _OldRegistry:
        def __init__(self) -> None:
            self.entries: dict[str, Any] = {}

        def register(self, name: str, handler: Any) -> None:
            self.entries[name] = handler

    state = _OldRegistry()
    n = register_ferret_slash(state)
    assert n == len(FERRET_SLASH_COMMANDS)
    for name, handler in FERRET_SLASH_COMMANDS.items():
        assert state.entries[name] is handler


def test_register_ferret_slash_against_shared_registry_lists_help() -> None:
    """End-to-end smoke: install onto the real shared registry, then /help."""
    from chimera.cli import slash_commands as shared

    register_ferret_slash(shared)
    names = {name for name, _ in shared.list_commands()}
    for must_have in ("sessions", "new", "share", "sandbox", "approval", "diff"):
        assert must_have in names

    out = _CapturePrinter()
    shared.cmd_help(None, None, "", out)
    rendered = "\n".join(out.lines)
    assert "/sandbox" in rendered
    assert "/approval" in rendered
    assert "/diff" in rendered
