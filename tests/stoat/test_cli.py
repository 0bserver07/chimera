"""Smoke tests for the ``chimera stoat`` CLI scaffold.

Covers:

* ``add_arguments`` registers the documented flag surface.
* ``chimera stoat --version`` exits 0 and prints ``chimera stoat 0.5.0``.
* ``--mode`` is validated by argparse's ``choices``.
* ``--shell-mode`` boolean flag is wired.
* Subcommand placeholders dispatch correctly:
  - ``serve`` / ``agents`` / ``bench`` print a stub message and exit 2.
  - ``sessions`` / ``share`` route through the sessions handler.
* ``-p`` without ``$STOAT_MODEL`` / API key surfaces a friendly error.

Tests stay lightweight: no live provider calls.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys

import pytest

from chimera.stoat import cli as stoat_cli

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_SEMVER_RE = re.compile(r"\d+\.\d+\.\d+")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chimera stoat")
    stoat_cli.add_arguments(parser)
    return parser


def test_add_arguments_registers_core_flags() -> None:
    """``add_arguments`` exposes every flag the spec promises."""
    parser = _build_parser()
    options: set[str] = set()
    for action in parser._actions:  # noqa: SLF001
        options.update(action.option_strings)
    expected = {
        "--version",
        "--model",
        "-p",
        "--print",
        "--mode",
        "--shell-mode",
        "--cwd",
        "--max-steps",
        "--allowed-tools",
        "--no-color",
        "--no-rich",
    }
    missing = expected - options
    assert not missing, f"missing flags on stoat parser: {sorted(missing)}"


def test_add_arguments_default_model_uses_env_then_fallback(monkeypatch) -> None:
    """``--model`` defaults to ``$STOAT_MODEL`` then ``_DEFAULT_MODEL``."""
    monkeypatch.delenv("STOAT_MODEL", raising=False)
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.model == stoat_cli._DEFAULT_MODEL  # noqa: SLF001
    monkeypatch.setenv("STOAT_MODEL", "kimi-k2-thinking")
    parser2 = _build_parser()
    args2 = parser2.parse_args([])
    assert args2.model == "kimi-k2-thinking"


def test_add_arguments_mode_choices() -> None:
    """``--mode`` rejects values outside the documented set."""
    parser = _build_parser()
    args = parser.parse_args(["--mode", "interactive"])
    assert args.mode == "interactive"
    args = parser.parse_args(["--mode", "rpc"])
    assert args.mode == "rpc"
    args = parser.parse_args(["--mode", "print"])
    assert args.mode == "print"
    with pytest.raises(SystemExit):
        parser.parse_args(["--mode", "bogus"])


def test_add_arguments_shell_mode_flag() -> None:
    """``--shell-mode`` is a boolean flag toggling the start mode."""
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.shell_mode is False
    args = parser.parse_args(["--shell-mode"])
    assert args.shell_mode is True


def test_version_subprocess_emits_zero_dot_five_dot_zero() -> None:
    """``chimera stoat --version`` exits 0 and prints the per-CLI version."""
    proc = subprocess.run(
        [sys.executable, "-m", "chimera.cli.main", "stoat", "--version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = (proc.stdout + proc.stderr).strip()
    assert out.startswith("chimera stoat ")
    assert "0.7.0" in out
    assert _SEMVER_RE.search(out) is not None


def test_run_dispatches_unknown_mode_print_without_prompt() -> None:
    """``--mode print`` without ``-p`` is a usage error (exit 2)."""
    parser = _build_parser()
    args = parser.parse_args(["--mode", "print"])
    rc = stoat_cli.run(args)
    assert rc == 2


def test_run_dispatches_serve_stub() -> None:
    """``stoat serve`` returns 2 (scaffold) without crashing."""
    parser = _build_parser()
    args = parser.parse_args(["serve"])
    rc = stoat_cli.run(args)
    assert rc == 2


def test_run_dispatches_agents_stub() -> None:
    """``stoat agents`` returns 2 (scaffold) without crashing."""
    parser = _build_parser()
    args = parser.parse_args(["agents"])
    rc = stoat_cli.run(args)
    assert rc == 2


def test_run_dispatches_bench_stub() -> None:
    """``stoat bench`` returns 2 (scaffold) without crashing."""
    parser = _build_parser()
    args = parser.parse_args(["bench"])
    rc = stoat_cli.run(args)
    assert rc == 2


def test_run_dispatches_sessions_list_to_handler(tmp_path, monkeypatch) -> None:
    """``stoat sessions list`` routes through the sessions module."""
    monkeypatch.setattr(
        "chimera.stoat.sessions.default_eventlog_root",
        lambda: tmp_path,
    )
    parser = _build_parser()
    args = parser.parse_args(["sessions", "list"])
    # Capture stdout so the table doesn't pollute pytest output.
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    rc = stoat_cli.run(args)
    assert rc == 0
    assert "no stoat sessions found" in buf.getvalue() or "session(s)" in buf.getvalue()


def test_filter_allowed_tools_unknown_raises() -> None:
    """``--allowed-tools`` with a bogus name raises ``_UnknownAllowedTool``."""

    class _ToolStub:
        def __init__(self, name: str) -> None:
            self.name = name

    tools = [_ToolStub("read"), _ToolStub("write")]
    with pytest.raises(stoat_cli._UnknownAllowedTool):  # noqa: SLF001
        stoat_cli._filter_allowed_tools(tools, "bogus")  # noqa: SLF001


def test_filter_allowed_tools_empty_returns_all() -> None:
    """Empty ``--allowed-tools`` is a no-op."""

    class _ToolStub:
        def __init__(self, name: str) -> None:
            self.name = name

    tools = [_ToolStub("read"), _ToolStub("write")]
    assert stoat_cli._filter_allowed_tools(tools, "") == tools  # noqa: SLF001


def test_filter_allowed_tools_subset() -> None:
    """``--allowed-tools`` filters to the named tools (case-insensitive)."""

    class _ToolStub:
        def __init__(self, name: str) -> None:
            self.name = name

    tools = [_ToolStub("Read"), _ToolStub("Write"), _ToolStub("Bash")]
    filtered = stoat_cli._filter_allowed_tools(tools, "read,bash")  # noqa: SLF001
    names = sorted(t.name.lower() for t in filtered)
    assert names == ["bash", "read"]
