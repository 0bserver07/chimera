"""Persistent shell sessions via tmux."""
from __future__ import annotations

import shutil
import subprocess
import uuid


class SessionMixin:
    """Mixin that adds persistent shell sessions to any Environment.

    Uses tmux as the backend. Supports multiple named shells (windows)
    within a single tmux session.

    Usage:
        class MyEnv(SessionMixin, Environment):
            ...

        env = MyEnv()
        env.start_session()
        # Now run_command() routes through the persistent shell
        env.end_session()
    """

    _session_name: str | None = None

    @property
    def has_session(self) -> bool:
        """Whether a persistent session is currently active."""
        return self._session_name is not None

    def start_session(self, shell: str = "/bin/bash") -> None:
        """Start a tmux session with a 'main' window.

        Raises RuntimeError if a session is already active.
        Raises FileNotFoundError if tmux is not installed.
        """
        if self.has_session:
            raise RuntimeError("Session already active")
        if shutil.which("tmux") is None:
            raise FileNotFoundError("tmux is not installed")

        self._session_name = f"chimera-{uuid.uuid4().hex[:8]}"
        subprocess.run(
            [
                "tmux", "new-session",
                "-d",  # detached
                "-s", self._session_name,
                "-n", "main",  # first window name
                shell,
            ],
            check=True,
            capture_output=True,
        )

    def end_session(self) -> None:
        """Kill the tmux session and all its windows."""
        if not self.has_session:
            return
        subprocess.run(
            ["tmux", "kill-session", "-t", self._session_name],
            capture_output=True,
        )
        self._session_name = None
