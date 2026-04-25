"""Audit B-2 / B-7 / B-8 regression tests: TUI render is wired into runtime.

Pins the user-visible behavior that ``chimera mink -p`` and ``chimera code``
(opt-in) actually instantiate :class:`MinkStreamHandler` instead of the
legacy :class:`ConsoleStreamHandler` when stdout is a TTY and color is not
disabled. Pipes, ``NO_COLOR``, and ``--no-color``/``--no-rich`` still
return the plain handler so logs stay diffable.
"""
from __future__ import annotations

import argparse
import io
from typing import Any

import pytest

# WHY: this file pins behavior that only holds when ``rich`` is installed —
# without the mink extra ``build_stream_handler`` returns the plain
# ConsoleStreamHandler in every branch (M3 fallback), so the assertions
# that demand a MinkStreamHandler would fail. Skip cleanly so the file
# stays opt-in alongside the extra.
pytest.importorskip("rich")


from chimera.cli.render import (
    MinkStreamHandler,
    build_stream_handler,
)
from chimera.streaming.handlers import ConsoleStreamHandler


# ---------------------------------------------------------------------------
# build_stream_handler routing
# ---------------------------------------------------------------------------


class _FakeTTYStream(io.StringIO):
    """StringIO that lies about being a TTY."""

    def isatty(self) -> bool:  # type: ignore[override]
        return True


class _FakePipeStream(io.StringIO):
    """StringIO that explicitly reports non-TTY."""

    def isatty(self) -> bool:  # type: ignore[override]
        return False


def test_build_stream_handler_returns_mink_on_tty(monkeypatch):
    """A fake TTY stream + no NO_COLOR → MinkStreamHandler."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    handler = build_stream_handler(stream=_FakeTTYStream(), no_color=False)
    assert isinstance(handler, MinkStreamHandler), type(handler)


def test_build_stream_handler_returns_console_when_pipe(monkeypatch):
    """Non-TTY stream → plain ConsoleStreamHandler regardless of NO_COLOR."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    handler = build_stream_handler(stream=_FakePipeStream(), no_color=False)
    assert isinstance(handler, ConsoleStreamHandler), type(handler)


def test_no_color_flag_forces_console(monkeypatch):
    """``--no-color`` (no_color=True) → ConsoleStreamHandler even on a TTY."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    handler = build_stream_handler(stream=_FakeTTYStream(), no_color=True)
    assert isinstance(handler, ConsoleStreamHandler), type(handler)


def test_no_color_env_var_forces_console(monkeypatch):
    """``$NO_COLOR=1`` → ConsoleStreamHandler even on a TTY."""
    monkeypatch.setenv("NO_COLOR", "1")
    handler = build_stream_handler(stream=_FakeTTYStream(), no_color=False)
    assert isinstance(handler, ConsoleStreamHandler), type(handler)


def test_force_rich_skips_tty_check(monkeypatch):
    """``force_rich=True`` returns MinkStreamHandler even for non-TTY."""
    monkeypatch.setenv("NO_COLOR", "1")  # would normally force console
    handler = build_stream_handler(stream=_FakePipeStream(), force_rich=True)
    assert isinstance(handler, MinkStreamHandler), type(handler)


# ---------------------------------------------------------------------------
# MinkStreamHandler tool-call rendering
# ---------------------------------------------------------------------------


def test_mink_streamhandler_emits_collapsed_tool_block():
    """``on_tool_start`` writes the collapsed ``▶ name(...)`` line."""
    sink = io.StringIO()
    handler = MinkStreamHandler(stream=sink)
    handler.on_tool_start("bash", call_id="call-1")
    handler.on_tool_end("call-1", "hello world")
    out = sink.getvalue()
    assert "bash" in out
    # The collapsed-line marker is the up-triangle prefix.
    assert "▶" in out, repr(out)  # ▶
    assert "hello world" in out


def test_mink_streamhandler_renders_diff_for_edit_tool(monkeypatch):
    """Edit calls with old/new args trigger the DiffRenderer palette."""
    sink = io.StringIO()
    handler = MinkStreamHandler(stream=sink)

    # Stash args via the same hook on_tool_start would: handler caches by
    # call_id. Public API on_tool_start does not currently take args, but
    # the handler exposes _call_args for test injection so the diff path
    # is exercised end-to-end.
    handler._call_args["call-7"] = {
        "file_path": "x.txt",
        "old_string": "alpha\nbeta\n",
        "new_string": "alpha\nBETA\n",
    }
    handler._call_names["call-7"] = "edit"
    handler.on_tool_end("call-7", output="ignored — diff path takes args")
    out = sink.getvalue()
    # DiffRenderer emits ANSI red/green ESC sequences. Check for the bold
    # ``--- a/x.txt`` / ``+++ b/x.txt`` headers and the changed line.
    assert "x.txt" in out
    assert "BETA" in out
    # Either the bold ESC or the green/red palette code must be present
    # for the diff rendering to be considered active.
    assert "\x1b[" in out, "no ANSI escape: diff path did not render"


# ---------------------------------------------------------------------------
# CLI wiring: --no-color routes through build_stream_handler
# ---------------------------------------------------------------------------


def test_mink_cli_no_color_flag_present():
    """argparse must accept ``--no-color`` after the wiring patch."""
    from chimera.mink.cli import add_arguments

    parser = argparse.ArgumentParser()
    add_arguments(parser)
    args = parser.parse_args(["--no-color", "-p", "noop"])
    assert args.no_color is True


def test_mink_print_uses_minkstreamhandler_on_tty(monkeypatch, tmp_path):
    """``_run_print_mode`` instantiates MinkStreamHandler when stdout is a TTY.

    Verified structurally rather than by running an LLM: stub the agent
    factory + provider, monkeypatch ``sys.stdout.isatty`` to True, and
    assert the LoopConfig.handler is a MinkStreamHandler.
    """
    from chimera.mink import cli as mink_cli

    monkeypatch.delenv("NO_COLOR", raising=False)
    captured: dict[str, Any] = {}

    class _StubProvider:
        model_name = "stub-model"

        async def async_run(self, prompt, env=None):  # pragma: no cover - not reached
            class _R:
                output = "ok"
                steps = 1
                cost = 0.0
                success = True
            return _R()

    class _StubAgent:
        def __init__(self, provider, tools, loop, prompt):
            captured["loop"] = loop
            captured["tools"] = tools
            captured["provider"] = provider
            self.provider = provider
            self.tools = tools
            self.loop = loop
            self.prompt = prompt

        async def async_run(self, prompt, env=None):
            class _R:
                output = "ok"
                steps = 1
                cost = 0.0
                success = True
            return _R()

    monkeypatch.setattr(mink_cli, "_build_provider", lambda model: _StubProvider())
    monkeypatch.setattr("chimera.core.agent.Agent", _StubAgent)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)

    args = argparse.Namespace(
        model="stub", permission_mode="bypassPermissions", allowed_tools="",
        resume=None, agent=None, cwd=str(tmp_path), print_mode="hi",
        output_format="text", max_steps=2, no_save=True, run_id=None,
        no_rich=False, no_color=False,
    )
    mink_cli._run_print_mode(args)

    loop = captured.get("loop")
    assert loop is not None, "Agent constructor was not called"
    handler = loop.config.handler
    assert isinstance(handler, MinkStreamHandler), (
        f"expected MinkStreamHandler, got {type(handler).__name__}"
    )


def test_mink_print_uses_console_when_pipe(monkeypatch, tmp_path):
    """Non-TTY stdout → ConsoleStreamHandler (back-compat for pipes)."""
    from chimera.mink import cli as mink_cli

    monkeypatch.delenv("NO_COLOR", raising=False)
    captured: dict[str, Any] = {}

    class _StubProvider:
        model_name = "stub-model"

    class _StubAgent:
        def __init__(self, provider, tools, loop, prompt):
            captured["loop"] = loop
            self.provider = provider
            self.tools = tools
            self.loop = loop
            self.prompt = prompt

        async def async_run(self, prompt, env=None):
            class _R:
                output = "ok"
                steps = 1
                cost = 0.0
                success = True
            return _R()

    monkeypatch.setattr(mink_cli, "_build_provider", lambda model: _StubProvider())
    monkeypatch.setattr("chimera.core.agent.Agent", _StubAgent)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False, raising=False)

    args = argparse.Namespace(
        model="stub", permission_mode="bypassPermissions", allowed_tools="",
        resume=None, agent=None, cwd=str(tmp_path), print_mode="hi",
        output_format="text", max_steps=2, no_save=True, run_id=None,
        no_rich=False, no_color=False,
    )
    mink_cli._run_print_mode(args)
    handler = captured["loop"].config.handler
    assert isinstance(handler, ConsoleStreamHandler), (
        f"expected ConsoleStreamHandler for piped stdout, got {type(handler).__name__}"
    )


def test_no_color_arg_forces_console_handler(monkeypatch, tmp_path):
    """``args.no_color=True`` forces ConsoleStreamHandler even with a TTY."""
    from chimera.mink import cli as mink_cli

    monkeypatch.delenv("NO_COLOR", raising=False)
    captured: dict[str, Any] = {}

    class _StubProvider:
        model_name = "stub-model"

    class _StubAgent:
        def __init__(self, provider, tools, loop, prompt):
            captured["loop"] = loop
            self.provider = provider
            self.tools = tools
            self.loop = loop
            self.prompt = prompt

        async def async_run(self, prompt, env=None):
            class _R:
                output = "ok"
                steps = 1
                cost = 0.0
                success = True
            return _R()

    monkeypatch.setattr(mink_cli, "_build_provider", lambda model: _StubProvider())
    monkeypatch.setattr("chimera.core.agent.Agent", _StubAgent)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)

    args = argparse.Namespace(
        model="stub", permission_mode="bypassPermissions", allowed_tools="",
        resume=None, agent=None, cwd=str(tmp_path), print_mode="hi",
        output_format="text", max_steps=2, no_save=True, run_id=None,
        no_rich=False, no_color=True,
    )
    mink_cli._run_print_mode(args)
    handler = captured["loop"].config.handler
    assert isinstance(handler, ConsoleStreamHandler), (
        f"expected ConsoleStreamHandler with --no-color, got {type(handler).__name__}"
    )


def test_json_output_format_keeps_handler_none(monkeypatch, tmp_path):
    """JSON / stream-json modes leave handler=None to avoid mixed output."""
    from chimera.mink import cli as mink_cli

    captured: dict[str, Any] = {}

    class _StubProvider:
        model_name = "stub-model"

    class _StubAgent:
        def __init__(self, provider, tools, loop, prompt):
            captured["loop"] = loop
            self.provider = provider
            self.tools = tools
            self.loop = loop
            self.prompt = prompt

        async def async_run(self, prompt, env=None):
            class _R:
                output = "ok"
                steps = 1
                cost = 0.0
                success = True
            return _R()

    monkeypatch.setattr(mink_cli, "_build_provider", lambda model: _StubProvider())
    monkeypatch.setattr("chimera.core.agent.Agent", _StubAgent)

    args = argparse.Namespace(
        model="stub", permission_mode="bypassPermissions", allowed_tools="",
        resume=None, agent=None, cwd=str(tmp_path), print_mode="hi",
        output_format="json", max_steps=2, no_save=True, run_id=None,
        no_rich=False, no_color=False,
    )
    mink_cli._run_print_mode(args)
    handler = captured["loop"].config.handler
    assert handler is None, f"json mode must keep handler=None, got {type(handler)}"
