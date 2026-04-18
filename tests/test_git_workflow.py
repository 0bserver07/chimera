"""Tests for git-aware workflow."""
from __future__ import annotations

import tempfile

import pytest

from chimera.env.git_env import GitEnvironment
from chimera.workflows.git_workflow import CommitStrategy, GitWorkflow


@pytest.fixture
def git_env():
    with tempfile.TemporaryDirectory() as tmp:
        env = GitEnvironment(workdir=tmp)
        env.setup()
        yield env
        env.cleanup()


class TestGitWorkflow:
    def test_start_creates_branch(self, git_env):
        wf = GitWorkflow(git_env)
        branch = wf.start("test-feature")
        assert branch == "chimera/test-feature"
        result = git_env._git("rev-parse --abbrev-ref HEAD")
        assert result.stdout.strip() == "chimera/test-feature"

    def test_start_auto_name(self, git_env):
        wf = GitWorkflow(git_env)
        branch = wf.start()
        assert branch.startswith("chimera/")

    def test_get_diff_context_empty(self, git_env):
        wf = GitWorkflow(git_env)
        wf.start("test")
        assert wf.get_diff_context() == ""

    def test_get_diff_context_with_changes(self, git_env):
        wf = GitWorkflow(git_env)
        wf.start("test")
        git_env.write_file("new.py", "print('hello')")
        git_env._git("add new.py")
        diff = wf.get_diff_context()
        assert "new.py" in diff

    def test_get_changed_files(self, git_env):
        wf = GitWorkflow(git_env)
        wf.start("test")
        git_env.write_file("a.py", "x = 1")
        wf.commit("add a.py")
        changed = wf.get_changed_files()
        assert "a.py" in changed

    def test_commit_returns_sha(self, git_env):
        wf = GitWorkflow(git_env)
        wf.start("test")
        git_env.write_file("x.py", "x = 1")
        sha = wf.commit("test commit")
        assert len(sha) == 40

    def test_finish_merges(self, git_env):
        wf = GitWorkflow(git_env)
        wf.start("test")
        git_env.write_file("merged.py", "merged = True")
        wf.commit("add merged")
        sha = wf.finish(merge=True)
        assert sha is not None
        # Should be back on original branch
        result = git_env._git("rev-parse --abbrev-ref HEAD")
        assert result.stdout.strip() != "chimera/test"
        # File should exist after merge
        content = git_env.read_file("merged.py")
        assert content == "merged = True"

    def test_finish_no_merge(self, git_env):
        wf = GitWorkflow(git_env)
        wf.start("test")
        result = wf.finish(merge=False)
        assert result is None

    def test_abort_discards_changes(self, git_env):
        wf = GitWorkflow(git_env)
        wf.start("test")
        git_env.write_file("temp.py", "temp = True")
        wf.commit("temp")
        wf.abort()
        result = git_env._git("rev-parse --abbrev-ref HEAD")
        assert "chimera/test" not in result.stdout

    def test_strategy_stored(self, git_env):
        wf = GitWorkflow(git_env, strategy=CommitStrategy.PER_STEP)
        assert wf.strategy == CommitStrategy.PER_STEP

    def test_branch_name_property(self, git_env):
        wf = GitWorkflow(git_env)
        assert wf.branch_name is None
        wf.start("prop-test")
        assert wf.branch_name == "chimera/prop-test"
