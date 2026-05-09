"""Smoke tests for the ``chimera badger`` CLI scaffold.

Covers:

* ``add_arguments`` registers the documented flag surface.
* ``chimera badger --version`` prints ``chimera badger 0.7.0``.
* ``chimera badger --help`` exits 0 and lists the load-bearing flags.
* Subcommand placeholders route through :func:`chimera.badger.cli.run`.
* ``--output-format`` is validated by argparse's ``choices``.
* ``--max-steps`` defaults to the documented harness-rewrite ceiling (25).

Tests stay lightweight: no live provider, no network.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

import pytest

from chimera.badger import cli as badger_cli

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_SEMVER_RE = re.compile(r"\d+\.\d+\.\d+")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chimera badger")
    badger_cli.add_arguments(parser)
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
        "--output-format",
        "--max-steps",
        "--cwd",
        "--allowed-tools",
        "--rerun-on-failure",
        "--max-reruns",
        "--no-rich",
        "--no-color",
        "--no-save",
        "--run-id",
        "--against",
    }
    missing = expected - options
    assert not missing, f"missing flags on badger parser: {sorted(missing)}"


def test_add_arguments_default_model_uses_env_then_fallback(monkeypatch) -> None:
    """``--model`` defaults to ``$BADGER_MODEL`` then ``_DEFAULT_MODEL``."""
    monkeypatch.delenv("BADGER_MODEL", raising=False)
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.model == badger_cli._DEFAULT_MODEL  # noqa: SLF001

    monkeypatch.setenv("BADGER_MODEL", "claude-opus-4-5")
    parser2 = _build_parser()
    args2 = parser2.parse_args([])
    assert args2.model == "claude-opus-4-5"


def test_default_max_steps_is_tighter_than_siblings() -> None:
    """The harness-rewrite default tightens to 25 (vs 50 elsewhere)."""
    assert badger_cli._DEFAULT_MAX_STEPS == 25  # noqa: SLF001
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.max_steps == 25


def test_default_model_anthropic_first() -> None:
    """The general-purpose Anthropic-first default ships an Anthropic id."""
    assert badger_cli._DEFAULT_MODEL.startswith("claude")  # noqa: SLF001


def test_output_format_choices_enforced() -> None:
    """``--output-format`` rejects unknown formats."""
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--output-format", "xml"])


def test_subcommand_choices_enforced() -> None:
    """Positional ``SUBCOMMAND`` is restricted to documented values."""
    parser = _build_parser()
    args = parser.parse_args(["parity"])
    assert args.subcommand == "parity"
    with pytest.raises(SystemExit):
        parser.parse_args(["bogus"])


def test_rerun_flag_default_false() -> None:
    """``--rerun-on-failure`` defaults off so rerun is opt-in."""
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.rerun_on_failure is False
    args2 = parser.parse_args(["--rerun-on-failure"])
    assert args2.rerun_on_failure is True


def test_max_reruns_default_two() -> None:
    """``--max-reruns`` defaults to 2 attempts."""
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.max_reruns == 2
    args2 = parser.parse_args(["--max-reruns", "5"])
    assert args2.max_reruns == 5


# ---------------------------------------------------------------------------
# Subprocess: chimera badger --version
# ---------------------------------------------------------------------------


def test_subprocess_version_prints_semver() -> None:
    """``chimera badger --version`` exits 0 and emits the documented prefix."""
    res = subprocess.run(
        [sys.executable, "-m", "chimera.cli.main", "badger", "--version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    out = res.stdout.strip()
    assert out.startswith("chimera badger "), out
    assert _SEMVER_RE.search(out), out


def test_subprocess_version_matches_release() -> None:
    """``chimera badger --version`` prints the release semver."""
    res = subprocess.run(
        [sys.executable, "-m", "chimera.cli.main", "badger", "--version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "chimera badger 0.7.0" == res.stdout.strip()


def test_subprocess_help_exits_zero() -> None:
    """``chimera badger --help`` prints help and exits 0."""
    res = subprocess.run(
        [sys.executable, "-m", "chimera.cli.main", "badger", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "--rerun-on-failure" in res.stdout
    assert "--against" in res.stdout
    assert "parity" in res.stdout


# ---------------------------------------------------------------------------
# Run dispatch — placeholders return rc=2.
# ---------------------------------------------------------------------------


def test_run_serve_returns_two() -> None:
    """``serve`` is a stub in the wave-9 scaffold; rc=2."""
    args = argparse.Namespace(
        subcommand="serve", sub_action=None, sub_target=None,
    )
    rc = badger_cli.run(args)
    assert rc == 2


def test_run_agents_returns_two() -> None:
    args = argparse.Namespace(
        subcommand="agents", sub_action="list", sub_target=None,
    )
    rc = badger_cli.run(args)
    assert rc == 2


def test_run_bench_returns_two() -> None:
    args = argparse.Namespace(
        subcommand="bench", sub_action="humaneval", sub_target=None,
    )
    rc = badger_cli.run(args)
    assert rc == 2


def test_run_print_missing_prompt_rc_two() -> None:
    """``-p`` with empty prompt is a usage error (rc=2)."""
    args = argparse.Namespace(
        subcommand=None, sub_action=None, sub_target=None,
        print_mode="", model="x", cwd=None, max_steps=25,
        output_format="text", rerun_on_failure=False, max_reruns=2,
        allowed_tools="",
    )
    rc = badger_cli.run(args)
    assert rc == 2


def test_filter_allowed_tools_unknown_raises() -> None:
    """Unknown tool name in ``--allowed-tools`` raises ``_UnknownAllowedTool``."""

    class _FakeTool:
        def __init__(self, name: str) -> None:
            self.name = name

    tools = [_FakeTool("read_file"), _FakeTool("bash")]
    with pytest.raises(badger_cli._UnknownAllowedTool):  # noqa: SLF001
        badger_cli._filter_allowed_tools(tools, "nonsense")  # noqa: SLF001


def test_filter_allowed_tools_matches_case_insensitive() -> None:
    class _FakeTool:
        def __init__(self, name: str) -> None:
            self.name = name

    tools = [_FakeTool("read_file"), _FakeTool("bash")]
    out = badger_cli._filter_allowed_tools(tools, "READ_FILE")  # noqa: SLF001
    assert len(out) == 1 and out[0].name == "read_file"


def test_filter_allowed_tools_empty_returns_all() -> None:
    class _FakeTool:
        def __init__(self, name: str) -> None:
            self.name = name

    tools = [_FakeTool("read_file"), _FakeTool("bash")]
    out = badger_cli._filter_allowed_tools(tools, "")  # noqa: SLF001
    assert len(out) == 2
