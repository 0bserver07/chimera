"""Tests for ``chimera.badger.repl`` — interactive REPL bootstrap."""

from __future__ import annotations

import argparse
from pathlib import Path

from chimera.badger import repl


def test_make_run_id_starts_with_prefix() -> None:
    rid = repl.make_run_id()
    assert rid.startswith("badger-")
    parts = rid.split("-")
    # badger-<timestamp>-<uuid8>
    assert len(parts) == 3
    assert len(parts[2]) == 8


def test_eventlog_root_under_home() -> None:
    root = repl.badger_eventlog_root()
    assert root.parts[-2:] == (".chimera", "eventlog")


def test_open_run_log_creates_dir(tmp_path: Path, monkeypatch) -> None:
    """``open_badger_run_log`` materializes the run directory."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    log, run_dir = repl.open_badger_run_log("badger-test-12345678")
    assert run_dir.exists()
    assert run_dir.name == "badger-test-12345678"


def test_shim_args_translates_to_run_code_namespace(tmp_path: Path) -> None:
    args = argparse.Namespace(
        model="claude-sonnet-4-6",
        cwd=str(tmp_path),
        max_steps=25,
        agent="reviewer",
    )
    shimmed = repl.shim_badger_args(args)
    assert shimmed.model == "claude-sonnet-4-6"
    assert shimmed.workdir == str(tmp_path.resolve())
    assert shimmed.max_steps == 25
    assert shimmed.mode == "interactive"
    assert shimmed.preset == "reviewer"
    assert shimmed.print_mode is None


def test_shim_args_defaults_max_steps() -> None:
    """``shim_badger_args`` defaults max_steps to 25 when missing."""
    args = argparse.Namespace()
    shimmed = repl.shim_badger_args(args)
    assert shimmed.max_steps == 25


def test_run_repl_returns_one_when_no_provider(tmp_path: Path, monkeypatch) -> None:
    """Missing API keys emit a friendly error and return 1."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for var in (
        "BADGER_MODEL",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "OLLAMA_HOST",
    ):
        monkeypatch.delenv(var, raising=False)
    args = argparse.Namespace(
        model=None, cwd=str(tmp_path), max_steps=25,
        agent=None, run_id="badger-test-norunner",
        _quiet_run_dir=True,
    )
    rc = repl.run_badger_repl(args)
    assert rc == 1
