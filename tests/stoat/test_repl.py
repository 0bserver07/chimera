"""Tests for the stoat REPL.

The REPL is exercised through :class:`StoatRepl`, which the public
:func:`run` entry point wraps. Tests inject a fake ``input`` function and
a :class:`io.StringIO` output sink so we can drive the loop without a TTY
and assert on the rendered transcript.

Slash palette under test (covered more deeply in ``test_slash.py``):

* ``/help`` — prints the slash help block.
* ``/exit`` — breaks the loop.
* ``/clear`` — drops conversation history.
* ``/model`` — shows / sets the active model id.
* ``/shell`` — toggles the shell-mode state machine.

Shell-mode dispatch is verified end-to-end against ``bash -c`` so the
``run_shell_turn`` integration is exercised.
"""

from __future__ import annotations

import io
import shutil

import pytest

from chimera.stoat import repl as stoat_repl
from chimera.stoat.repl import StoatRepl
from chimera.stoat.shell_mode import MODE_AGENT, MODE_SHELL


def _make_repl(
    inputs: list[str],
    *,
    model: str | None = "kimi-k2.6",
    max_steps: int = 50,
    start_in_shell_mode: bool = False,
) -> tuple[StoatRepl, io.StringIO]:
    """Build a :class:`StoatRepl` driven by a scripted input list."""
    out = io.StringIO()
    iterator = iter(inputs)

    def fake_input(_prompt: str) -> str:
        try:
            return next(iterator)
        except StopIteration as exc:
            raise EOFError() from exc

    repl = StoatRepl(
        model=model,
        workdir=".",
        max_steps=max_steps,
        out=out,
        input_fn=fake_input,
        start_in_shell_mode=start_in_shell_mode,
    )
    return repl, out


def test_repl_starts_in_agent_mode_by_default() -> None:
    """A fresh REPL boots in agent mode unless told otherwise."""
    repl, _ = _make_repl([])
    assert repl.shell_mode.mode == MODE_AGENT


def test_repl_can_start_in_shell_mode() -> None:
    """``start_in_shell_mode=True`` boots into shell mode."""
    repl, _ = _make_repl([], start_in_shell_mode=True)
    assert repl.shell_mode.mode == MODE_SHELL


def test_help_slash_prints_palette() -> None:
    """``/help`` writes the slash palette banner."""
    repl, out = _make_repl(["/help"])
    rc = repl.run()
    assert rc == 0
    text = out.getvalue()
    assert "/shell" in text
    assert "/clear" in text


def test_exit_slash_returns_zero() -> None:
    """``/exit`` exits the loop with rc=0."""
    repl, _ = _make_repl(["/exit"])
    assert repl.run() == 0


def test_eof_exits_cleanly() -> None:
    """An EOF (no more inputs) exits the loop with rc=0."""
    repl, _ = _make_repl([])
    assert repl.run() == 0


def test_clear_slash_drops_history() -> None:
    """``/clear`` removes any accumulated conversation history."""
    repl, _ = _make_repl(["/clear", "/exit"])
    repl.history.append(("user", "hi"))
    repl.history.append(("assistant", "hello"))
    repl.run()
    assert repl.history == []


def test_model_slash_shows_and_sets() -> None:
    """``/model`` prints the active model; ``/model <id>`` sets it."""
    repl, out = _make_repl(["/model", "/model gpt-4o", "/model", "/exit"])
    repl.run()
    text = out.getvalue()
    assert "kimi-k2.6" in text
    assert "model set: gpt-4o" in text
    assert repl.model == "gpt-4o"


def test_shell_slash_toggles_mode() -> None:
    """``/shell`` flips between agent and shell modes."""
    repl, out = _make_repl(["/shell", "/shell", "/exit"])
    repl.run()
    text = out.getvalue()
    # First /shell -> shell mode banner; second -> agent mode.
    assert "shell mode" in text
    assert "agent mode" in text
    # Final state after two toggles is agent mode again.
    assert repl.shell_mode.is_agent_mode()


def test_unknown_slash_renders_help() -> None:
    """An unknown slash command surfaces a hint listing valid commands."""
    repl, out = _make_repl(["/bogus", "/exit"])
    repl.run()
    text = out.getvalue()
    assert "unknown command: /bogus" in text


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="bash not available on PATH",
)
def test_shell_mode_input_runs_bash_command() -> None:
    """In shell mode, plain input runs as ``bash -c <input>``."""
    repl, out = _make_repl(
        ["echo stoat-shell-marker", "/exit"],
        start_in_shell_mode=True,
    )
    repl.run()
    text = out.getvalue()
    assert "stoat-shell-marker" in text


def test_blank_input_is_ignored() -> None:
    """Submitting a blank line just re-prompts."""
    repl, _ = _make_repl(["", "/exit"])
    assert repl.run() == 0


def test_run_entry_point_uses_namespace_attrs(monkeypatch) -> None:
    """:func:`run` reads model / cwd / max_steps / shell_mode / plan_mode."""
    captured: dict[str, object] = {}

    class _StubRepl:
        def __init__(
            self,
            *,
            model,
            workdir,
            max_steps,
            start_in_shell_mode,
            start_in_plan_mode=False,
        ) -> None:
            captured["model"] = model
            captured["workdir"] = workdir
            captured["max_steps"] = max_steps
            captured["start_in_shell_mode"] = start_in_shell_mode
            captured["start_in_plan_mode"] = start_in_plan_mode

        def run(self) -> int:
            return 0

    monkeypatch.setattr(stoat_repl, "StoatRepl", _StubRepl)

    import argparse

    args = argparse.Namespace(
        model="kimi-k2.6",
        cwd="/tmp",
        max_steps=10,
        shell_mode=True,
        plan_mode=False,
    )
    rc = stoat_repl.run(args)
    assert rc == 0
    assert captured["model"] == "kimi-k2.6"
    assert captured["workdir"] == "/tmp"
    assert captured["max_steps"] == 10
    assert captured["start_in_shell_mode"] is True
    assert captured["start_in_plan_mode"] is False
