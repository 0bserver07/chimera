"""Tests for the ``/diff`` slash command (G9, w13).

Covers:

* Clean working tree → friendly "no diff" message.
* Modified file → ``git diff HEAD`` body lands on the print sink.
* ``/diff stat`` flips to ``--stat`` summary mode.
* ``/diff <path>`` narrows the diff to a single file.
* ``FileTracker``-attached session narrows the no-arg diff to the
  tracked file set.
* Non-git directory surfaces a friendly "not a git repository" line.
* The badger palette wires ``/diff`` and the shared registry exposes it.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from chimera.cli import slash_commands as sc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_GIT_AVAILABLE = shutil.which("git") is not None


class _Recorder:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)

    @property
    def joined(self) -> str:
        return "\n".join(self.lines)


class _Env:
    """Tiny env stand-in carrying just ``workdir``."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = str(workdir)


def _git(*args: str, cwd: Path) -> None:
    """Run a git command, failing the test on non-zero rc."""
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a fresh git repo with one committed file."""
    if not _GIT_AVAILABLE:
        pytest.skip("git not on PATH")
    _git("init", "-q", cwd=tmp_path)
    _git("config", "user.email", "chimera@local", cwd=tmp_path)
    _git("config", "user.name", "Chimera Test", cwd=tmp_path)
    (tmp_path / "hello.py").write_text("print('hello')\n")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-q", "-m", "init", cwd=tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# /diff body
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _GIT_AVAILABLE, reason="git not on PATH")
def test_diff_clean_tree_says_no_changes(git_repo: Path) -> None:
    rec = _Recorder()
    sc.cmd_diff(session=None, env=_Env(git_repo), args="", out=rec)
    body = rec.joined
    assert "No diff" in body or "clean" in body.lower(), body


@pytest.mark.skipif(not _GIT_AVAILABLE, reason="git not on PATH")
def test_diff_shows_modified_file(git_repo: Path) -> None:
    (git_repo / "hello.py").write_text("print('hello world')\n")
    rec = _Recorder()
    sc.cmd_diff(session=None, env=_Env(git_repo), args="", out=rec)
    body = rec.joined
    assert "diff --git" in body, body
    assert "hello world" in body, body


@pytest.mark.skipif(not _GIT_AVAILABLE, reason="git not on PATH")
def test_diff_stat_emits_summary(git_repo: Path) -> None:
    (git_repo / "hello.py").write_text("print('hello world')\n")
    rec = _Recorder()
    sc.cmd_diff(session=None, env=_Env(git_repo), args="stat", out=rec)
    body = rec.joined
    # ``--stat`` output ends with the canonical summary line.
    assert "1 file changed" in body or "insertion" in body, body
    # Full unified diff body must NOT be present when only --stat ran.
    assert "diff --git" not in body, body


@pytest.mark.skipif(not _GIT_AVAILABLE, reason="git not on PATH")
def test_diff_narrows_to_path_argument(git_repo: Path) -> None:
    (git_repo / "hello.py").write_text("print('hello world')\n")
    (git_repo / "second.py").write_text("print('second')\n")
    _git("add", "second.py", cwd=git_repo)
    _git("commit", "-q", "-m", "second", cwd=git_repo)
    (git_repo / "second.py").write_text("print('second changed')\n")

    rec = _Recorder()
    sc.cmd_diff(session=None, env=_Env(git_repo), args="hello.py", out=rec)
    body = rec.joined
    assert "hello.py" in body
    # second.py changed but the path filter should hide it.
    assert "second.py" not in body, body


@pytest.mark.skipif(not _GIT_AVAILABLE, reason="git not on PATH")
def test_diff_uses_file_tracker_scope_when_present(git_repo: Path) -> None:
    """A session ``file_tracker.modified_files`` narrows the no-arg diff."""
    (git_repo / "hello.py").write_text("print('hello world')\n")
    (git_repo / "second.py").write_text("print('second')\n")
    _git("add", "second.py", cwd=git_repo)
    _git("commit", "-q", "-m", "second", cwd=git_repo)
    (git_repo / "second.py").write_text("print('second touched')\n")

    class _Tracker:
        modified_files: set[str] = {"hello.py"}

    class _Session:
        file_tracker = _Tracker()

    rec = _Recorder()
    sc.cmd_diff(session=_Session(), env=_Env(git_repo), args="", out=rec)
    body = rec.joined
    assert "hello.py" in body
    assert "second.py" not in body, body
    # Banner advertises the scoped diff so users can tell.
    assert "scope" in body.lower()


@pytest.mark.skipif(not _GIT_AVAILABLE, reason="git not on PATH")
def test_diff_outside_git_repo_friendly_error(tmp_path: Path) -> None:
    rec = _Recorder()
    sc.cmd_diff(session=None, env=_Env(tmp_path), args="", out=rec)
    body = rec.joined
    assert "not a git repository" in body or "not available" in body, body


def test_diff_without_git_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``git`` isn't on PATH, ``/diff`` says so without raising."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    rec = _Recorder()
    sc.cmd_diff(session=None, env=_Env(Path.cwd()), args="", out=rec)
    body = rec.joined
    assert "not available" in body
    assert "git" in body


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_diff_registered_in_shared_registry() -> None:
    names = {name for name, _ in sc.list_commands()}
    assert "diff" in names


def test_diff_registered_in_badger_palette() -> None:
    from chimera.badger import slash as badger_slash

    assert "diff" in badger_slash.BADGER_SLASH_COMMANDS
    assert "diff" in badger_slash.BADGER_SLASH_HELP
    assert badger_slash.BADGER_SLASH_COMMANDS["diff"] is sc.cmd_diff


@pytest.mark.skipif(not _GIT_AVAILABLE, reason="git not on PATH")
def test_diff_dispatches_via_shared_registry(git_repo: Path) -> None:
    rec = _Recorder()
    handled = sc.dispatch(
        "/diff",
        session=None,
        env=_Env(git_repo),
        out=rec,
    )
    assert handled
    body = rec.joined
    assert "No diff" in body or "diff --git" in body, body
