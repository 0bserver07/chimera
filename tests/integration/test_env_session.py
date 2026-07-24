# tests/test_env_session.py
"""Tests for SessionMixin persistent shell."""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from chimera.env.session import SessionMixin

# Skip all tests if tmux is not installed
pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None,
    reason="tmux not installed",
)


class ConcreteSession(SessionMixin):
    """Minimal concrete class for testing the mixin."""
    pass


class TestSessionLifecycle:
    def test_has_session_false_by_default(self):
        s = ConcreteSession()
        assert s.has_session is False

    def test_start_creates_tmux_session(self):
        s = ConcreteSession()
        try:
            s.start_session()
            assert s.has_session is True
            # Verify tmux session exists
            result = subprocess.run(
                ["tmux", "has-session", "-t", s._session_name],
                capture_output=True,
            )
            assert result.returncode == 0
        finally:
            s.end_session()

    def test_end_kills_tmux_session(self):
        s = ConcreteSession()
        s.start_session()
        name = s._session_name
        s.end_session()
        assert s.has_session is False
        result = subprocess.run(
            ["tmux", "has-session", "-t", name],
            capture_output=True,
        )
        assert result.returncode != 0

    def test_end_session_when_no_session_is_noop(self):
        s = ConcreteSession()
        s.end_session()  # Should not raise

    def test_double_start_raises(self):
        s = ConcreteSession()
        s.start_session()
        try:
            with pytest.raises(RuntimeError, match="already active"):
                s.start_session()
        finally:
            s.end_session()


class TestNamedShells:
    def test_main_shell_exists_after_start(self):
        s = ConcreteSession()
        s.start_session()
        try:
            assert "main" in s.list_shells()
        finally:
            s.end_session()

    def test_create_shell(self):
        s = ConcreteSession()
        s.start_session()
        try:
            s.create_shell("server")
            shells = s.list_shells()
            assert "main" in shells
            assert "server" in shells
        finally:
            s.end_session()

    def test_create_duplicate_shell_raises(self):
        s = ConcreteSession()
        s.start_session()
        try:
            with pytest.raises(ValueError, match="already exists"):
                s.create_shell("main")
        finally:
            s.end_session()

    def test_create_shell_without_session_raises(self):
        s = ConcreteSession()
        with pytest.raises(RuntimeError, match="No active session"):
            s.create_shell("test")


class TestRunInSession:
    def test_simple_echo(self):
        s = ConcreteSession()
        s.start_session()
        try:
            result = s.run_in_session("echo hello")
            assert result.success
            assert "hello" in result.stdout
        finally:
            s.end_session()

    def test_exit_code_captured(self):
        s = ConcreteSession()
        s.start_session()
        try:
            result = s.run_in_session("false")
            assert not result.success
            assert result.exit_code != 0
        finally:
            s.end_session()

    def test_cd_persists(self):
        s = ConcreteSession()
        s.start_session()
        try:
            s.run_in_session("cd /tmp")
            result = s.run_in_session("pwd")
            assert result.success
            # /tmp may resolve to /private/tmp on macOS
            assert "tmp" in result.stdout
        finally:
            s.end_session()

    def test_export_persists(self):
        s = ConcreteSession()
        s.start_session()
        try:
            s.run_in_session("export CHIMERA_TEST_VAR=hello42")
            result = s.run_in_session("echo $CHIMERA_TEST_VAR")
            assert "hello42" in result.stdout
        finally:
            s.end_session()

    def test_named_shells_are_independent(self):
        s = ConcreteSession()
        s.start_session()
        try:
            s.create_shell("other")
            s.run_in_session("cd /tmp", shell_name="main")
            result = s.run_in_session("pwd", shell_name="other")
            # 'other' shell should NOT be in /tmp
            assert result.stdout.strip() != "/tmp"
            assert result.stdout.strip() != "/private/tmp"
        finally:
            s.end_session()

    def test_timeout(self):
        s = ConcreteSession()
        s.start_session()
        try:
            result = s.run_in_session("sleep 60", timeout=1)
            assert result.exit_code != 0  # non-zero on timeout (124 or 1 depending on platform)
        finally:
            s.end_session()

    def test_run_without_session_raises(self):
        s = ConcreteSession()
        with pytest.raises(RuntimeError, match="No active session"):
            s.run_in_session("echo hi")

    def test_multiline_output(self):
        s = ConcreteSession()
        s.start_session()
        try:
            result = s.run_in_session("echo line1; echo line2; echo line3")
            assert "line1" in result.stdout
            assert "line2" in result.stdout
            assert "line3" in result.stdout
        finally:
            s.end_session()


class TestHistoryIsolation:
    """Session shells must never write to the user's real shell history."""

    def test_spawned_shell_has_isolated_histfile(self):
        s = ConcreteSession()
        s.start_session()
        try:
            assert s._session_env_dir is not None
            result = s.run_in_session("echo HISTFILE=$HISTFILE")
            assert s._session_env_dir in result.stdout
        finally:
            s.end_session()

    def test_new_windows_inherit_isolation(self):
        s = ConcreteSession()
        s.start_session()
        try:
            s.create_shell("aux")
            result = s.run_in_session("echo HISTFILE=$HISTFILE", shell_name="aux")
            assert s._session_env_dir is not None
            assert s._session_env_dir in result.stdout
        finally:
            s.end_session()

    def test_isolation_can_be_disabled(self):
        s = ConcreteSession()
        s.start_session(isolate_history=False)
        try:
            assert s._session_env_dir is None
        finally:
            s.end_session()

    def test_end_session_removes_env_dir(self):
        s = ConcreteSession()
        s.start_session()
        env_dir = s._session_env_dir
        assert env_dir is not None
        assert os.path.isdir(env_dir)
        s.end_session()
        assert s._session_env_dir is None
        assert not os.path.exists(env_dir)
