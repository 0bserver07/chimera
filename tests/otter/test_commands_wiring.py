"""Tests for the ``.opencode/command/*.md`` -> slash registry wiring (W4).

This suite verifies the bridge between
:mod:`chimera.otter.commands` (custom-command loader) and
:mod:`chimera.otter.slash` (slash-command palette + installer):

1. **Schema** — :func:`build_custom_command_handler` returns a callable
   matching the canonical ``(session, env, args, out)`` shape.
2. **Registry** — :func:`register_custom_commands` lands every
   :class:`CustomCommand` on a register-style state, a dict-style state,
   and the real :mod:`chimera.cli.slash_commands` registry.
3. **register_otter_slash extension** — passing ``custom_commands=[...]``
   installs the customs on top of the built-in palette.
4. **Behavior** — invoking a registered handler renders the body
   template with positional + ``key=value`` args and pushes the result
   to ``session.queue`` (preferred) / ``session.steer`` (fallback).
5. **REPL wiring** — :func:`run_otter_repl` honors the project-scope
   ``.opencode/command/*.md`` files at startup, except when
   ``--no-custom-commands`` is set.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

from chimera.otter.commands import CustomCommand, CustomCommandArg
from chimera.otter.slash import (
    OTTER_SLASH_COMMANDS,
    build_custom_command_handler,
    register_custom_commands,
    register_otter_slash,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

class _Captured:
    """Tiny callable that records each line printed by a handler."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str = "") -> None:
        self.lines.append(line)


class _QueueSession:
    """Session fake that records ``queue()`` calls (the preferred sink)."""

    def __init__(self) -> None:
        self.queued: list[str] = []
        self.steered: list[str] = []

    def queue(self, message: str) -> None:
        self.queued.append(message)

    def steer(self, message: str) -> None:
        self.steered.append(message)


class _SteerOnlySession:
    """Session fake that only exposes ``steer()`` (the fallback sink)."""

    def __init__(self) -> None:
        self.steered: list[str] = []

    def steer(self, message: str) -> None:
        self.steered.append(message)


class _BareSession:
    """Session fake exposing neither queue nor steer."""


class _RegistryState:
    """Mimics the shared :mod:`chimera.cli.slash_commands` register API."""

    def __init__(self) -> None:
        self.entries: dict[str, tuple[Any, str]] = {}

    def register(self, name: str, handler: Any, help_text: str = "") -> None:
        self.entries[name] = (handler, help_text)


class _DictState:
    """REPL state that exposes commands as a plain dict."""

    def __init__(self) -> None:
        self.commands: dict[str, Any] = {}


def _make_three_commands() -> list[CustomCommand]:
    """Three synthetic custom commands covering positional, named, plain."""
    return [
        CustomCommand(
            name="review",
            description="Review a target file",
            args=[CustomCommandArg(name="target", description="path")],
            body_template="Please review $1 thoroughly.",
            source="/fake/.opencode/command/review.md",
        ),
        CustomCommand(
            name="summarize",
            description="Summarize a topic",
            args=[CustomCommandArg(name="topic")],
            body_template="Summarize the topic: $TOPIC ($ARGUMENTS)",
            source="/fake/.opencode/command/summarize.md",
        ),
        CustomCommand(
            name="ping",
            description="A static template",
            args=[],
            body_template="pong",
            source="/fake/.opencode/command/ping.md",
        ),
    ]


# ---------------------------------------------------------------------------
# build_custom_command_handler
# ---------------------------------------------------------------------------

def test_handler_is_callable_with_canonical_shape() -> None:
    """The wrapped handler accepts ``(session, env, args, out)`` like the rest."""
    cmd = _make_three_commands()[0]
    handler = build_custom_command_handler(cmd)
    assert callable(handler)
    # Call with all four positional arguments — must not raise.
    handler(_QueueSession(), None, "src/main.py", _Captured())


def test_handler_renders_positional_arg_into_template() -> None:
    """``$1`` resolves from the first positional token."""
    cmd = _make_three_commands()[0]
    handler = build_custom_command_handler(cmd)
    session = _QueueSession()
    out = _Captured()
    handler(session, None, "src/main.py", out)
    assert session.queued == ["Please review src/main.py thoroughly."]
    # ``out`` carries the "queued" confirmation, not the rendered body.
    assert any("queued" in line for line in out.lines)


def test_handler_supports_named_kv_arg_and_arguments() -> None:
    """``key=value`` becomes a named arg; ``$ARGUMENTS`` joins positionals."""
    cmd = _make_three_commands()[1]
    handler = build_custom_command_handler(cmd)
    session = _QueueSession()
    handler(session, None, 'extra topic="machine learning"', _Captured())
    # ``$TOPIC`` came in via ``topic="machine learning"`` (case-insensitive).
    # ``$ARGUMENTS`` is the joined positional string -> "extra".
    assert session.queued == [
        "Summarize the topic: machine learning (extra)"
    ]


def test_handler_handles_empty_args() -> None:
    """A static template with no args still pushes the rendered body."""
    cmd = _make_three_commands()[2]
    handler = build_custom_command_handler(cmd)
    session = _QueueSession()
    handler(session, None, "", _Captured())
    assert session.queued == ["pong"]


def test_handler_falls_back_to_steer_when_queue_missing() -> None:
    """Without ``queue``, we route through ``steer`` instead."""
    cmd = _make_three_commands()[2]
    handler = build_custom_command_handler(cmd)
    session = _SteerOnlySession()
    handler(session, None, "", _Captured())
    assert session.steered == ["pong"]


def test_handler_falls_back_to_print_when_session_bare() -> None:
    """Without queue or steer the rendered text is printed via ``out``."""
    cmd = _make_three_commands()[2]
    handler = build_custom_command_handler(cmd)
    out = _Captured()
    handler(_BareSession(), None, "", out)
    assert "pong" in out.lines


def test_handler_never_raises_on_render_failure() -> None:
    """A render error is reported, not propagated."""
    cmd = CustomCommand(
        name="broken",
        body_template="ok",
        source="/fake/broken.md",
    )

    class _BoomCommand(CustomCommand):
        def render(self, *positional: str, **named: str) -> str:
            raise RuntimeError("intentional boom")

    bad = _BoomCommand(name=cmd.name, body_template=cmd.body_template)
    handler = build_custom_command_handler(bad)
    out = _Captured()
    # No exception must escape — we should see a diagnostic line instead.
    handler(_QueueSession(), None, "", out)
    assert any("render failed" in line for line in out.lines)


# ---------------------------------------------------------------------------
# register_custom_commands
# ---------------------------------------------------------------------------

def test_register_custom_commands_lands_on_register_state() -> None:
    """Each command is installed via the ``register(name, handler, help)`` API."""
    state = _RegistryState()
    cmds = _make_three_commands()
    n = register_custom_commands(state, cmds)
    assert n == len(cmds)
    for cmd in cmds:
        assert cmd.name in state.entries
        handler, help_text = state.entries[cmd.name]
        assert callable(handler)
        # Help text comes from the description (or a default).
        assert help_text == cmd.description


def test_register_custom_commands_lands_on_dict_state() -> None:
    """A state with a ``commands`` dict gets populated in place."""
    state = _DictState()
    cmds = _make_three_commands()
    n = register_custom_commands(state, cmds)
    assert n == len(cmds)
    for cmd in cmds:
        assert cmd.name in state.commands
        assert callable(state.commands[cmd.name])


def test_register_custom_commands_empty_list_is_noop() -> None:
    """An empty list installs nothing and reports zero."""
    state = _RegistryState()
    assert register_custom_commands(state, []) == 0
    assert state.entries == {}


def test_register_custom_commands_against_shared_registry() -> None:
    """End-to-end: customs land on the real shared slash registry."""
    from chimera.cli import slash_commands as shared

    cmds = _make_three_commands()
    register_custom_commands(shared, cmds)
    names = {name for name, _ in shared.list_commands()}
    for cmd in cmds:
        assert cmd.name in names

    # /help renders the new entries.
    out = _Captured()
    shared.cmd_help(None, None, "", out)
    rendered = "\n".join(out.lines)
    for cmd in cmds:
        assert f"/{cmd.name}" in rendered


# ---------------------------------------------------------------------------
# register_otter_slash extension
# ---------------------------------------------------------------------------

def test_register_otter_slash_with_custom_commands_installs_both() -> None:
    """Built-ins land first, then customs — total = built-ins + customs."""
    state = _RegistryState()
    cmds = _make_three_commands()
    n = register_otter_slash(state, custom_commands=cmds)
    assert n == len(OTTER_SLASH_COMMANDS) + len(cmds)
    # Built-ins present.
    assert "help" in state.entries
    # Customs present.
    for cmd in cmds:
        assert cmd.name in state.entries


def test_register_otter_slash_without_custom_commands_default() -> None:
    """When ``custom_commands`` is ``None``, only built-ins are installed."""
    state = _RegistryState()
    n = register_otter_slash(state)
    assert n == len(OTTER_SLASH_COMMANDS)
    assert "review" not in state.entries  # one of our synthetic customs


def test_register_otter_slash_custom_overrides_builtin() -> None:
    """A custom command with a built-in name clobbers the built-in (last wins)."""
    state = _RegistryState()
    shadow = CustomCommand(
        name="help",
        description="user-defined help override",
        body_template="custom help body",
    )
    register_otter_slash(state, custom_commands=[shadow])
    handler, help_text = state.entries["help"]
    # The override's help text and handler win.
    assert help_text == "user-defined help override"
    # Smoke-test the handler runs the custom render path.
    session = _QueueSession()
    handler(session, None, "", _Captured())
    assert session.queued == ["custom help body"]


# ---------------------------------------------------------------------------
# Smoke: end-to-end .md file -> shared registry via REPL helpers
# ---------------------------------------------------------------------------

def test_load_otter_custom_commands_reads_project_dir(tmp_path: Path) -> None:
    """Project ``.opencode/command/*.md`` files surface via the REPL helper."""
    cmd_dir = tmp_path / ".opencode" / "command"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "scan.md").write_text(
        "---\n"
        "description: Scan a path for issues\n"
        "args:\n"
        "  - name: target\n"
        "    description: file or dir\n"
        "---\n"
        "Scan $1 for issues.\n",
        encoding="utf-8",
    )

    from chimera.otter.repl import load_otter_custom_commands

    customs = load_otter_custom_commands(tmp_path)
    names = [c.name for c in customs]
    assert "scan" in names
    cmd = next(c for c in customs if c.name == "scan")
    assert cmd.description == "Scan a path for issues"
    assert "$1" in cmd.body_template


def test_run_otter_repl_registers_custom_commands(tmp_path: Path) -> None:
    """``run_otter_repl`` calls register_custom_commands by default."""
    # Create a project-scope command file.
    cmd_dir = tmp_path / ".opencode" / "command"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "ping.md").write_text(
        "---\ndescription: Ping handler\n---\npong\n",
        encoding="utf-8",
    )

    # Reset the shared registry so we can observe the install cleanly.
    from chimera.cli import slash_commands as shared

    shared._REGISTRY.clear()  # type: ignore[attr-defined]
    shared._build_default_registry()  # type: ignore[attr-defined]

    from unittest.mock import MagicMock

    fake_provider = MagicMock()
    fake_provider.model_name = "synthetic-test-model"

    with patch.dict(os.environ, {"HOME": str(tmp_path)}, clear=False):
        with patch("chimera.otter.repl.Path") as mock_path_cls:
            real_path = Path
            mock_path_cls.side_effect = lambda *a, **kw: real_path(*a, **kw)
            mock_path_cls.home.return_value = real_path(tmp_path)
            with patch(
                "chimera.otter.repl._build_otter_provider",
                return_value=fake_provider,
            ):
                with patch("chimera.cli.code.run_code", return_value=0):
                    from chimera.otter.repl import run_otter_repl

                    args = argparse.Namespace(
                        model="synthetic-test-model",
                        cwd=str(tmp_path),
                        max_steps=5,
                        agent=None,
                        models="",
                        no_custom_commands=False,
                        _quiet_run_dir=True,
                    )
                    rc = run_otter_repl(args)
    assert rc == 0
    names = {name for name, _ in shared.list_commands()}
    assert "ping" in names


def test_run_otter_repl_skips_when_no_custom_commands_flag(tmp_path: Path) -> None:
    """``--no-custom-commands`` short-circuits the loader path."""
    cmd_dir = tmp_path / ".opencode" / "command"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "shouldnotload.md").write_text(
        "---\ndescription: skipped\n---\nbody\n",
        encoding="utf-8",
    )

    from chimera.cli import slash_commands as shared

    shared._REGISTRY.clear()  # type: ignore[attr-defined]
    shared._build_default_registry()  # type: ignore[attr-defined]

    from unittest.mock import MagicMock

    fake_provider = MagicMock()
    fake_provider.model_name = "synthetic-test-model"

    with patch.dict(os.environ, {"HOME": str(tmp_path)}, clear=False):
        with patch("chimera.otter.repl.Path") as mock_path_cls:
            real_path = Path
            mock_path_cls.side_effect = lambda *a, **kw: real_path(*a, **kw)
            mock_path_cls.home.return_value = real_path(tmp_path)
            with patch(
                "chimera.otter.repl._build_otter_provider",
                return_value=fake_provider,
            ):
                with patch("chimera.cli.code.run_code", return_value=0):
                    from chimera.otter.repl import run_otter_repl

                    args = argparse.Namespace(
                        model="synthetic-test-model",
                        cwd=str(tmp_path),
                        max_steps=5,
                        agent=None,
                        models="",
                        no_custom_commands=True,
                        _quiet_run_dir=True,
                    )
                    rc = run_otter_repl(args)
    assert rc == 0
    names = {name for name, _ in shared.list_commands()}
    # The file existed, but the flag suppressed registration.
    assert "shouldnotload" not in names
