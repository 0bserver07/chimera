"""Tests for chimera.tools.worktree_tool."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from chimera.tools.worktree_tool import (
    EnterWorktreeTool,
    ExitWorktreeTool,
    _is_git_repo,
)


def _git(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Create an isolated git repo with one commit so worktrees have a base."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], cwd=str(repo))
    _git(["config", "user.email", "t@e.com"], cwd=str(repo))
    _git(["config", "user.name", "T"], cwd=str(repo))
    (repo / "README.md").write_text("hi\n")
    _git(["add", "."], cwd=str(repo))
    _git(["commit", "-q", "-m", "init"], cwd=str(repo))
    return repo


def test_enter_then_exit(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(git_repo)
    if not _is_git_repo():
        pytest.skip("not a git repo")

    enter = EnterWorktreeTool()
    res = enter.execute({"name": "feature-x"}, env=None)
    assert res.success, res.error
    wt_path = res.output
    assert os.path.isdir(wt_path)

    listing = _git(["worktree", "list"], cwd=str(git_repo)).stdout
    assert "feature-x" in listing

    ext = ExitWorktreeTool()
    res2 = ext.execute({"worktree_path": wt_path, "action": "remove"}, env=None)
    assert res2.success, res2.error
    listing2 = _git(["worktree", "list"], cwd=str(git_repo)).stdout
    assert "feature-x" not in listing2


def test_remove_refuses_dirty(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(git_repo)
    enter = EnterWorktreeTool()
    res = enter.execute({"name": "dirty"}, env=None)
    assert res.success
    wt_path = Path(res.output)
    (wt_path / "scratch.txt").write_text("uncommitted\n")

    ext = ExitWorktreeTool()
    res2 = ext.execute({"worktree_path": str(wt_path), "action": "remove"}, env=None)
    assert not res2.success
    assert "uncommitted" in (res2.error or "")

    # Cleanup so other tests don't inherit a leftover worktree.
    ext.execute({"worktree_path": str(wt_path), "action": "abandon"}, env=None)


def test_enter_outside_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # not a git repo
    enter = EnterWorktreeTool()
    res = enter.execute({"name": "anything"}, env=None)
    assert not res.success
    assert "git" in (res.error or "").lower()
