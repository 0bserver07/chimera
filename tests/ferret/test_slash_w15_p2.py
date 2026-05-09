"""Tests for the ferret W15-2 P2 slash additions: /copy, /rename, /status, /diff fallback.

Each test covers one of the four CODEX-gap items shipped in wave-15:

* CODEX #25 — ``/diff`` falls back to ``git diff`` + untracked listing
  when no tracker / hook is wired and the cwd is a git repo.
* CODEX #26 — ``/copy`` exports the latest assistant text to the
  platform clipboard via ``pbcopy`` / ``xclip`` / ``wl-copy`` /
  ``clip.exe``. We monkeypatch :mod:`subprocess` so the tests are
  hermetic.
* CODEX #38 — ``/status`` extends the shared status with the ferret
  posture (sandbox, approval, AGENTS.md presence).
* CODEX #39 — ``/rename`` updates ``session.title`` (or ``session.name``).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from chimera.ferret.slash import (
    FERRET_SLASH_COMMANDS,
    FERRET_SLASH_HELP,
    cmd_copy,
    cmd_diff,
    cmd_rename,
    cmd_status,
)


class _CapturePrinter:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str = "") -> None:
        self.lines.append(line)


class _FakeSession:
    def __init__(self) -> None:
        self.context = None
        self.provider = None
        self.cost_tracker = None
        self.sandbox_mode = "workspace-write"
        self.approval_preset = "auto"
        self.file_tracker = None
        self.messages: list[Any] = []
        self.title: str = ""


# ---------------------------------------------------------------------------
# /copy (CODEX #26)
# ---------------------------------------------------------------------------


def test_copy_reports_no_assistant_output() -> None:
    out = _CapturePrinter()
    cmd_copy(_FakeSession(), None, "", out)
    assert any("nothing to copy" in line for line in out.lines)


def test_copy_pipes_assistant_text_to_pbcopy(monkeypatch) -> None:
    sess = _FakeSession()
    sess.messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello world"},
    ]
    captured: dict[str, Any] = {}

    def fake_run(argv, *, input=None, text=None, check=None, timeout=None):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        captured["input"] = input
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = _CapturePrinter()
    cmd_copy(sess, None, "", out)
    assert captured["input"] == "hello world"
    assert any("copied 11 chars" in line for line in out.lines)


def test_copy_falls_through_when_no_clipboard_tool(monkeypatch) -> None:
    sess = _FakeSession()
    sess.messages = [{"role": "assistant", "content": "ping"}]

    def fake_run(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = _CapturePrinter()
    cmd_copy(sess, None, "", out)
    assert any("failed" in line for line in out.lines)


def test_copy_handles_block_shaped_content(monkeypatch) -> None:
    sess = _FakeSession()
    sess.messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "abc"}, {"type": "text", "text": "def"}]},
    ]
    captured: dict[str, Any] = {}

    def fake_run(argv, *, input=None, text=None, check=None, timeout=None):  # type: ignore[no-untyped-def]
        captured["input"] = input
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = _CapturePrinter()
    cmd_copy(sess, None, "", out)
    assert captured["input"] == "abc\ndef"


# ---------------------------------------------------------------------------
# /rename (CODEX #39)
# ---------------------------------------------------------------------------


def test_rename_with_no_arg_prints_current_title() -> None:
    sess = _FakeSession()
    sess.title = "old"
    out = _CapturePrinter()
    cmd_rename(sess, None, "", out)
    rendered = "\n".join(out.lines)
    assert "old" in rendered
    assert "usage" in rendered.lower()


def test_rename_sets_title() -> None:
    sess = _FakeSession()
    sess.title = "old"
    out = _CapturePrinter()
    cmd_rename(sess, None, "Wave 15 work", out)
    assert sess.title == "Wave 15 work"
    assert any("Wave 15 work" in line for line in out.lines)


def test_rename_truncates_long_titles() -> None:
    sess = _FakeSession()
    sess.title = ""
    out = _CapturePrinter()
    cmd_rename(sess, None, "x" * 250, out)
    assert isinstance(sess.title, str)
    assert len(sess.title) == 200


def test_rename_falls_back_to_name_attribute() -> None:
    class NameOnly:
        def __init__(self) -> None:
            self.name = "untitled"

    sess = NameOnly()
    out = _CapturePrinter()
    cmd_rename(sess, None, "wave-15", out)
    assert sess.name == "wave-15"


# ---------------------------------------------------------------------------
# /status (CODEX #38)
# ---------------------------------------------------------------------------


def test_status_appends_ferret_posture(tmp_path: Path) -> None:
    sess = _FakeSession()
    sess.sandbox_mode = "read-only"
    sess.approval_preset = "auto"

    class FakeEnv:
        workdir = str(tmp_path)

    out = _CapturePrinter()
    cmd_status(sess, FakeEnv(), "", out)
    rendered = "\n".join(out.lines)
    assert "Ferret posture" in rendered
    assert "sandbox:" in rendered
    assert "approval:" in rendered
    assert "AGENTS.md" in rendered


def test_status_marks_agents_md_present(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Project rules\n")
    sess = _FakeSession()

    class FakeEnv:
        workdir = str(tmp_path)

    out = _CapturePrinter()
    cmd_status(sess, FakeEnv(), "", out)
    rendered = "\n".join(out.lines)
    assert "AGENTS.md:  present" in rendered


def test_status_marks_agents_md_absent(tmp_path: Path) -> None:
    sess = _FakeSession()

    class FakeEnv:
        workdir = str(tmp_path)

    out = _CapturePrinter()
    cmd_status(sess, FakeEnv(), "", out)
    rendered = "\n".join(out.lines)
    assert "AGENTS.md:  absent" in rendered


# ---------------------------------------------------------------------------
# /diff git fallback (CODEX #25)
# ---------------------------------------------------------------------------


def test_diff_git_fallback_lists_untracked(tmp_path: Path, monkeypatch) -> None:
    """In a git repo with no tracker info, /diff lists untracked files."""
    # Build a tiny repo
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        subprocess.run(["git", "init", "-q"], check=True, cwd=tmp_path)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit",
             "--allow-empty", "-q", "-m", "init"],
            check=True,
            cwd=tmp_path,
        )
        (tmp_path / "new_file.txt").write_text("hi\n")

        class FakeEnv:
            workdir = str(tmp_path)

        sess = _FakeSession()
        out = _CapturePrinter()
        cmd_diff(sess, FakeEnv(), "", out)
        rendered = "\n".join(out.lines)
        assert "Untracked files" in rendered
        assert "new_file.txt" in rendered
    finally:
        os.chdir(cwd)


def test_diff_no_changes_in_clean_dir(tmp_path: Path) -> None:
    """A non-git directory with no tracker still degrades cleanly."""
    sess = _FakeSession()

    class FakeEnv:
        workdir = str(tmp_path)

    out = _CapturePrinter()
    cmd_diff(sess, FakeEnv(), "", out)
    rendered = "\n".join(out.lines)
    assert "no pending changes" in rendered


# ---------------------------------------------------------------------------
# Palette wiring
# ---------------------------------------------------------------------------


def test_new_commands_are_registered_in_palette() -> None:
    for name in ("copy", "rename", "status"):
        assert name in FERRET_SLASH_COMMANDS, f"missing /{name}"
        assert name in FERRET_SLASH_HELP, f"missing help for /{name}"


def test_status_help_mentions_ferret_posture() -> None:
    assert "sandbox" in FERRET_SLASH_HELP["status"].lower()
