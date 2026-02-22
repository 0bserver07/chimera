"""Persistent shell sessions via tmux."""
from __future__ import annotations

import shutil
import subprocess
import time
import uuid

from chimera.types import CommandResult


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

    def create_shell(self, name: str) -> None:
        """Create a new named shell (tmux window).

        Raises RuntimeError if no session is active.
        Raises ValueError if a shell with that name already exists.
        """
        if not self.has_session:
            raise RuntimeError("No active session")
        if name in self.list_shells():
            raise ValueError(f"Shell '{name}' already exists")
        subprocess.run(
            ["tmux", "new-window", "-t", self._session_name, "-n", name],
            check=True,
            capture_output=True,
        )

    def list_shells(self) -> list[str]:
        """List names of all active shells in the session."""
        if not self.has_session:
            return []
        result = subprocess.run(
            [
                "tmux", "list-windows",
                "-t", self._session_name,
                "-F", "#{window_name}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        return [
            line.strip() for line in result.stdout.strip().split("\n")
            if line.strip()
        ]

    def run_in_session(
        self,
        cmd: str,
        shell_name: str = "main",
        timeout: int = 120,
    ) -> CommandResult:
        """Run a command in a named shell and capture output.

        Uses sentinel markers to detect command completion and extract
        output. Polls tmux capture-pane until the end sentinel appears.

        Args:
            cmd: The shell command to execute.
            shell_name: Which tmux window to run in (default: "main").
            timeout: Max seconds to wait for completion.

        Returns:
            CommandResult with stdout, stderr, and exit_code.

        Raises:
            RuntimeError: If no session is active.
        """
        if not self.has_session:
            raise RuntimeError("No active session")

        marker = uuid.uuid4().hex[:12]
        start_sentinel = f"__CHIMERA_START__{marker}"
        end_sentinel = f"__CHIMERA_END__{marker}"

        # Wrap command with sentinels. The end sentinel includes the exit code.
        wrapped = (
            f"echo {start_sentinel}; "
            f"{{ {cmd} ; }}; "
            f"echo {end_sentinel}_$?"
        )

        target = f"{self._session_name}:{shell_name}"

        # Send the command
        subprocess.run(
            ["tmux", "send-keys", "-t", target, wrapped, "Enter"],
            check=True,
            capture_output=True,
        )

        # Poll for completion
        deadline = time.monotonic() + timeout
        poll_interval = 0.05  # Start at 50ms
        captured = ""

        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            result = subprocess.run(
                [
                    "tmux", "capture-pane",
                    "-t", target,
                    "-p",       # print to stdout
                    "-S", "-",  # capture from start of scrollback
                ],
                capture_output=True,
                text=True,
            )
            captured = result.stdout

            if f"{end_sentinel}_" in captured:
                break

            # Back off: 50ms -> 100ms -> 200ms -> 500ms (cap)
            poll_interval = min(poll_interval * 2, 0.5)
        else:
            # Timeout: send Ctrl-C to stop the command
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "C-c", ""],
                capture_output=True,
            )
            return CommandResult(
                stdout="",
                stderr="Command timed out",
                exit_code=124,
            )

        # Parse output between sentinels
        return self._parse_session_output(captured, start_sentinel, end_sentinel)

    def _parse_session_output(
        self,
        captured: str,
        start_sentinel: str,
        end_sentinel: str,
    ) -> CommandResult:
        """Extract command output and exit code from captured pane text."""
        lines = captured.split("\n")

        # Find sentinel positions — match only lines where the sentinel
        # is the *entire* stripped content (i.e. the actual echo output),
        # not lines where it appears embedded in the command prompt echo.
        start_idx = None
        end_idx = None
        exit_code = 0

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == start_sentinel and start_idx is None:
                start_idx = i
            if stripped.startswith(f"{end_sentinel}_"):
                end_idx = i
                # Parse exit code: __CHIMERA_END__<marker>_<code>
                try:
                    exit_code = int(stripped.split("_")[-1])
                except (ValueError, IndexError):
                    exit_code = 1

        if start_idx is None or end_idx is None:
            return CommandResult(stdout=captured, stderr="", exit_code=1)

        # Output is between start and end sentinels (exclusive)
        output_lines = lines[start_idx + 1 : end_idx]
        stdout = "\n".join(output_lines)

        return CommandResult(stdout=stdout, stderr="", exit_code=exit_code)
