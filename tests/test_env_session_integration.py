# tests/test_env_session_integration.py
"""Integration tests: LocalEnvironment + SessionMixin."""
from __future__ import annotations

import shutil
import tempfile

import pytest

from chimera.env.local import LocalEnvironment

pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None,
    reason="tmux not installed",
)


@pytest.fixture
def session_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment(workdir=tmpdir, session=True)
        env.setup()
        yield env
        env.cleanup()


@pytest.fixture
def stateless_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment(workdir=tmpdir, session=False)
        env.setup()
        yield env
        env.cleanup()


class TestLocalSessionIntegration:
    def test_session_env_has_active_session(self, session_env):
        assert session_env.has_session is True

    def test_stateless_env_has_no_session(self, stateless_env):
        assert stateless_env.has_session is False

    def test_cd_persists_with_session(self, session_env):
        session_env.run_command("cd /tmp")
        result = session_env.run_command("pwd")
        assert "tmp" in result.stdout

    def test_cd_does_not_persist_without_session(self, stateless_env):
        stateless_env.run_command("cd /tmp")
        result = stateless_env.run_command("pwd")
        # Should be in workdir, not /tmp
        assert "tmp" not in result.stdout or stateless_env.workdir.name in result.stdout

    def test_export_persists_with_session(self, session_env):
        session_env.run_command("export CHIMERA_INT_TEST=works99")
        result = session_env.run_command("echo $CHIMERA_INT_TEST")
        assert "works99" in result.stdout

    def test_cleanup_kills_session(self, session_env):
        assert session_env.has_session is True
        session_env.cleanup()
        assert session_env.has_session is False

    def test_run_command_with_named_shell(self, session_env):
        session_env.create_shell("worker")
        session_env.run_command("cd /tmp", shell_name="main")
        result = session_env.run_command("pwd", shell_name="worker")
        # worker shell should NOT be in /tmp
        stdout = result.stdout.strip()
        assert stdout != "/tmp" and stdout != "/private/tmp"

    def test_file_ops_still_work_with_session(self, session_env):
        """File operations are filesystem-based, unaffected by session."""
        session_env.write_file("test.txt", "hello")
        assert session_env.read_file("test.txt") == "hello"
