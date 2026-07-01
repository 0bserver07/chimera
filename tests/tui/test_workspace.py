"""Isolation tests for per-lane workspaces (spec §6.2 / §9, R-ISO-1)."""
import shutil
import subprocess

import pytest

from chimera.tui.workspace import (
    WorkspaceError,
    is_git_repo,
    provision_workspaces,
    resolve_strategy,
)

_HAS_GIT = shutil.which("git") is not None
requires_git = pytest.mark.skipif(not _HAS_GIT, reason="git not installed")


def _init_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "shared.txt").write_text("base\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)


@requires_git
def test_worktree_isolation_no_cross_contamination(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    assert is_git_repo(repo)
    assert resolve_strategy(repo, "auto") == "worktree"

    ws = provision_workspaces(repo, ["A", "B"], "auto")
    try:
        assert ws.strategy == "worktree"
        a, b = ws[0], ws[1]
        # Two lanes write DIFFERENT content to the SAME relative path.
        (a.path / "shared.txt").write_text("lane A\n")
        (b.path / "shared.txt").write_text("lane B DIFFERENT\n")
        (a.path / "new_a.py").write_text("print(1)\n")

        # R-ISO-1: no lane observes another's writes; source is untouched.
        assert (a.path / "shared.txt").read_text() == "lane A\n"
        assert (b.path / "shared.txt").read_text() == "lane B DIFFERENT\n"
        assert not (b.path / "new_a.py").exists()
        assert (repo / "shared.txt").read_text() == "base\n"

        # Diff captures modified + untracked changes.
        diff_a = a.diff()
        assert "lane A" in diff_a and "new_a.py" in diff_a
    finally:
        ws.cleanup_all()

    assert not ws[0].path.exists() and not ws[1].path.exists()
    branches = subprocess.run(
        ["git", "branch", "--list", "chimera-lane-*"],
        cwd=repo, capture_output=True, text=True,
    ).stdout
    assert branches.strip() == ""  # lane branches deleted


def test_copy_isolation_for_non_git_dir(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "f.txt").write_text("orig\n")
    assert not is_git_repo(src)
    assert resolve_strategy(src, "auto") == "copy"

    with provision_workspaces(src, ["X", "Y"], "auto") as ws:
        assert ws.strategy == "copy"
        (ws[0].path / "f.txt").write_text("changed by X\n")
        (ws[0].path / "brand_new.txt").write_text("new\n")
        # Y is isolated from X's writes.
        assert (ws[1].path / "f.txt").read_text() == "orig\n"
        # Snapshot diff reports the changed + added files.
        diff_x = ws[0].diff()
        assert "modified: f.txt" in diff_x
        assert "added:    brand_new.txt" in diff_x
    # context manager cleaned the copies up
    assert not (tmp_path / "proj_copies").exists()  # sanity: temp root removed


def test_worktree_strategy_rejects_non_git(tmp_path):
    src = tmp_path / "plain"
    src.mkdir()
    with pytest.raises(WorkspaceError):
        resolve_strategy(src, "worktree")


def test_inplace_shares_source(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "f.txt").write_text("x\n")
    ws = provision_workspaces(src, ["A", "B"], "inplace")
    try:
        assert ws[0].path == src and ws[1].path == src
    finally:
        ws.cleanup_all()
    # inplace never deletes the user's tree
    assert src.exists() and (src / "f.txt").exists()
