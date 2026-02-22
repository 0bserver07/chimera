# tests/test_env_session.py
"""Tests for SessionMixin persistent shell."""
from __future__ import annotations

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
