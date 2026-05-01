"""Tests for the stoat shell-mode state machine.

Covers:

* Mode toggling between agent and shell.
* Prompt prefix selection per mode.
* History recording (bounded, mode-tagged, oldest-evicted).
* ``run_shell`` end-to-end against ``bash -c``: success, stderr, return
  codes, empty command no-op.
"""

from __future__ import annotations

import os
import shutil

import pytest

from chimera.stoat.shell_mode import (
    MODE_AGENT,
    MODE_SHELL,
    ShellModeManager,
    ShellResult,
)


def test_default_mode_is_agent() -> None:
    """A fresh manager defaults to agent mode."""
    mgr = ShellModeManager()
    assert mgr.mode == MODE_AGENT
    assert mgr.is_agent_mode()
    assert not mgr.is_shell_mode()


def test_toggle_swaps_mode() -> None:
    """``toggle`` flips between agent and shell modes."""
    mgr = ShellModeManager()
    assert mgr.toggle() == MODE_SHELL
    assert mgr.is_shell_mode()
    assert mgr.toggle() == MODE_AGENT
    assert mgr.is_agent_mode()


def test_set_mode_validates() -> None:
    """``set_mode`` rejects invalid values."""
    mgr = ShellModeManager()
    mgr.set_mode(MODE_SHELL)
    assert mgr.mode == MODE_SHELL
    with pytest.raises(ValueError):
        mgr.set_mode("bogus")


def test_init_validates_mode() -> None:
    """Constructing with an unknown mode raises ``ValueError``."""
    with pytest.raises(ValueError):
        ShellModeManager(mode="unknown")


def test_init_validates_history_cap() -> None:
    """Non-positive ``history_cap`` is rejected."""
    with pytest.raises(ValueError):
        ShellModeManager(history_cap=0)
    with pytest.raises(ValueError):
        ShellModeManager(history_cap=-1)


def test_prompt_changes_per_mode() -> None:
    """The ``prompt`` property follows the active mode."""
    mgr = ShellModeManager()
    assert mgr.prompt == "stoat> "
    mgr.toggle()
    assert mgr.prompt == "stoat$ "


def test_prompt_can_be_overridden() -> None:
    """Custom prompts on construction are honored."""
    mgr = ShellModeManager(agent_prompt="A> ", shell_prompt="S$ ")
    assert mgr.prompt == "A> "
    mgr.toggle()
    assert mgr.prompt == "S$ "


def test_record_skips_empty_lines() -> None:
    """``record`` ignores empty / whitespace lines."""
    mgr = ShellModeManager()
    mgr.record("")
    assert len(mgr.history) == 0
    mgr.record("ls")
    assert mgr.history[-1] == (MODE_AGENT, "ls")


def test_history_evicts_oldest_when_capped() -> None:
    """``history_cap`` evicts oldest entries on overflow."""
    mgr = ShellModeManager(history_cap=2)
    mgr.record("a")
    mgr.record("b")
    mgr.record("c")
    assert list(mgr.history) == [(MODE_AGENT, "b"), (MODE_AGENT, "c")]


def test_recent_returns_last_n() -> None:
    """``recent(n)`` returns the n most recent entries."""
    mgr = ShellModeManager()
    for ch in "abcd":
        mgr.record(ch)
    assert mgr.recent(2) == [(MODE_AGENT, "c"), (MODE_AGENT, "d")]
    assert mgr.recent(0) == []
    assert len(mgr.recent(99)) == 4


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="bash not available on PATH",
)
def test_run_shell_captures_stdout() -> None:
    """``run_shell`` returns stdout from a successful command."""
    mgr = ShellModeManager()
    result = mgr.run_shell("echo hello-stoat")
    assert isinstance(result, ShellResult)
    assert result.ok
    assert result.returncode == 0
    assert "hello-stoat" in result.stdout
    assert result.command == "echo hello-stoat"


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="bash not available on PATH",
)
def test_run_shell_returns_nonzero_for_failure() -> None:
    """A failing command surfaces the non-zero exit code."""
    mgr = ShellModeManager()
    result = mgr.run_shell("exit 7")
    assert not result.ok
    assert result.returncode == 7


def test_run_shell_empty_command_short_circuits() -> None:
    """An empty command line is a no-op success (exit 0)."""
    mgr = ShellModeManager()
    result = mgr.run_shell("")
    assert result.ok
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.command == ""


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="bash not available on PATH",
)
def test_run_shell_respects_cwd(tmp_path) -> None:
    """``cwd`` is forwarded to the subprocess."""
    mgr = ShellModeManager()
    sentinel = tmp_path / "marker"
    sentinel.write_text("ok")
    result = mgr.run_shell("ls marker", cwd=str(tmp_path))
    assert result.ok, result.stderr
    assert "marker" in result.stdout


def test_run_shell_handles_missing_bash(monkeypatch) -> None:
    """A bogus ``$BASH`` path returns rc=127 with a helpful stderr."""
    mgr = ShellModeManager()
    monkeypatch.setenv(
        "BASH", os.path.join("nonexistent-bash-binary-stoat-test"),
    )
    result = mgr.run_shell("echo hi")
    assert not result.ok
    assert result.returncode == 127
    assert "bash not found" in result.stderr
