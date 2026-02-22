"""Tests for GitEnvironment (real git, temporary directories)."""
from __future__ import annotations

import tempfile

import pytest

from chimera.env.git_env import GitEnvironment


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = GitEnvironment(workdir=tmpdir, test_cmd="echo no tests")
        e.setup()
        yield e
        e.cleanup()


class TestGitEnvironmentSetup:
    def test_setup_creates_git_repo(self, env: GitEnvironment):
        assert (env.workdir / ".git").exists()

    def test_setup_has_initial_commit(self, env: GitEnvironment):
        result = env.run_command("git log --oneline")
        assert result.success
        assert "initial" in result.stdout


class TestGitEnvironmentCheckpoint:
    def test_checkpoint_creates_commit(self, env: GitEnvironment):
        env.write_file("data.txt", "hello")
        cp = env.checkpoint()
        # cp should be a git SHA
        assert len(cp) >= 7

    def test_restore_reverts_files(self, env: GitEnvironment):
        env.write_file("data.txt", "v1")
        cp = env.checkpoint()

        env.write_file("data.txt", "v2")
        assert env.read_file("data.txt") == "v2"

        env.restore(cp)
        assert env.read_file("data.txt") == "v1"

    def test_multiple_checkpoints(self, env: GitEnvironment):
        env.write_file("data.txt", "v1")
        cp1 = env.checkpoint()

        env.write_file("data.txt", "v2")
        cp2 = env.checkpoint()

        env.write_file("data.txt", "v3")
        assert env.read_file("data.txt") == "v3"

        env.restore(cp1)
        assert env.read_file("data.txt") == "v1"

        env.restore(cp2)
        assert env.read_file("data.txt") == "v2"

    def test_restore_removes_new_files(self, env: GitEnvironment):
        cp = env.checkpoint()  # Clean state

        env.write_file("new_file.txt", "content")
        assert env.read_file("new_file.txt") == "content"

        env.restore(cp)
        with pytest.raises(FileNotFoundError):
            env.read_file("new_file.txt")


def test_git_env_has_session_attr():
    """GitEnvironment inherits session support from LocalEnvironment."""
    import shutil
    import tempfile
    from chimera.env.git_env import GitEnvironment
    with tempfile.TemporaryDirectory() as tmpdir:
        env = GitEnvironment(workdir=tmpdir, session=False)
        assert hasattr(env, "has_session")
        assert env.has_session is False
