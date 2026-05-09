"""Tests for the badger W15-2 P2 additions: /teleport slash + --profile flag.

Covers:

* CLAW G14 — ``--profile {strict,balanced,yolo}`` bundles
  ``permission_mode`` / ``max_steps`` / ``rerun_on_failure`` /
  ``max_reruns``. Explicit flags always win.
* CLAW G23 — ``/teleport <symbol-or-path>`` resolves a target via
  filesystem walk over Python/JS/TS/Rust files.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from chimera.badger import cli as badger_cli
from chimera.badger.slash import (
    BADGER_SLASH_COMMANDS,
    BADGER_SLASH_HELP,
    cmd_teleport,
)


class _CapturePrinter:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str = "") -> None:
        self.lines.append(line)


# ---------------------------------------------------------------------------
# /teleport (CLAW G23)
# ---------------------------------------------------------------------------


def test_teleport_missing_arg_prints_usage() -> None:
    out = _CapturePrinter()
    cmd_teleport(None, None, "", out)
    assert any("missing" in line for line in out.lines)


def test_teleport_finds_python_def(tmp_path: Path) -> None:
    (tmp_path / "alpha.py").write_text("def hello_target():\n    return 1\n")
    (tmp_path / "beta.py").write_text("def other():\n    pass\n")

    class FakeEnv:
        workdir = str(tmp_path)

    out = _CapturePrinter()
    cmd_teleport(None, FakeEnv(), "hello_target", out)
    rendered = "\n".join(out.lines)
    assert "alpha.py:1" in rendered
    assert "hello_target" in rendered


def test_teleport_finds_existing_file(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("name: test\n")

    class FakeEnv:
        workdir = str(tmp_path)

    out = _CapturePrinter()
    cmd_teleport(None, FakeEnv(), "config.yaml", out)
    rendered = "\n".join(out.lines)
    assert "config.yaml" in rendered


def test_teleport_no_results(tmp_path: Path) -> None:
    (tmp_path / "alpha.py").write_text("def something_else():\n    return 1\n")

    class FakeEnv:
        workdir = str(tmp_path)

    out = _CapturePrinter()
    cmd_teleport(None, FakeEnv(), "no_such_symbol_anywhere", out)
    rendered = "\n".join(out.lines)
    assert "no results" in rendered


def test_teleport_skips_dotgit(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "ignore_me.py").write_text("def hello_target():\n    pass\n")
    (tmp_path / "found.py").write_text("def hello_target():\n    pass\n")

    class FakeEnv:
        workdir = str(tmp_path)

    out = _CapturePrinter()
    cmd_teleport(None, FakeEnv(), "hello_target", out)
    rendered = "\n".join(out.lines)
    assert "found.py" in rendered
    assert "ignore_me" not in rendered


def test_teleport_registered_in_palette() -> None:
    assert "teleport" in BADGER_SLASH_COMMANDS
    assert "teleport" in BADGER_SLASH_HELP


# ---------------------------------------------------------------------------
# --profile (CLAW G14)
# ---------------------------------------------------------------------------


def _empty_args() -> argparse.Namespace:
    """Mirrors badger's argparse defaults."""
    return argparse.Namespace(
        profile=None,
        permission_mode="suggest",
        max_steps=25,  # _DEFAULT_MAX_STEPS
        rerun_on_failure=False,
        max_reruns=0,
    )


def test_apply_profile_no_op_when_unset() -> None:
    args = _empty_args()
    out = badger_cli.apply_profile(args)
    assert out.permission_mode == "suggest"
    assert out.max_steps == 25
    assert out.rerun_on_failure is False


def test_profile_strict_locks_down() -> None:
    args = _empty_args()
    args.profile = "strict"
    badger_cli.apply_profile(args)
    assert args.permission_mode == "read-only"
    assert args.max_steps == 15
    assert args.rerun_on_failure is True


def test_profile_yolo_loosens() -> None:
    args = _empty_args()
    args.profile = "yolo"
    badger_cli.apply_profile(args)
    assert args.permission_mode == "yolo"
    assert args.max_steps == 50
    assert args.rerun_on_failure is False


def test_profile_balanced_matches_defaults() -> None:
    args = _empty_args()
    args.profile = "balanced"
    badger_cli.apply_profile(args)
    assert args.permission_mode == "suggest"
    assert args.max_steps == 25
    assert args.rerun_on_failure is True


def test_explicit_flags_override_profile() -> None:
    args = _empty_args()
    args.profile = "strict"
    args.permission_mode = "yolo"  # explicit
    args.max_steps = 99  # explicit (not _DEFAULT_MAX_STEPS)
    badger_cli.apply_profile(args)
    assert args.permission_mode == "yolo"
    assert args.max_steps == 99


def test_unknown_profile_is_no_op() -> None:
    args = _empty_args()
    args.profile = "doesnotexist"
    badger_cli.apply_profile(args)
    # No mutation
    assert args.permission_mode == "suggest"
    assert args.max_steps == 25


def test_profile_registered_as_choice() -> None:
    assert "strict" in badger_cli._VALID_PROFILES
    assert "balanced" in badger_cli._VALID_PROFILES
    assert "yolo" in badger_cli._VALID_PROFILES
