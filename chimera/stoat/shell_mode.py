"""Shell-mode state machine for the stoat REPL.

Stoat's headline ergonomic is the **shell-mode toggle**: a single
``Ctrl-X`` (or ``/shell`` slash) flips the REPL between two modes:

* ``agent`` — input is sent to the LLM agent (the default).
* ``shell`` — input runs as a direct shell command (``bash -c <input>``).

The state machine is intentionally tiny: it tracks the current mode, a
distinct prompt prefix per mode, and a shared command history. The REPL
asks the manager which prompt to render and which side of the toggle to
call when the user submits a line.

Trademark hygiene: the toggle is described as "shell mode" everywhere;
the upstream brand that pioneered the ergonomic is never named in source.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections import deque
from dataclasses import dataclass, field
from typing import Deque

__all__ = [
    "ShellModeManager",
    "ShellResult",
    "MODE_AGENT",
    "MODE_SHELL",
]


MODE_AGENT = "agent"
"""Identifier for agent mode: input is sent to the LLM."""

MODE_SHELL = "shell"
"""Identifier for shell mode: input runs as a direct shell command."""

_VALID_MODES = (MODE_AGENT, MODE_SHELL)

_DEFAULT_AGENT_PROMPT = "stoat> "
_DEFAULT_SHELL_PROMPT = "stoat$ "

_DEFAULT_HISTORY_CAP = 1000


@dataclass
class ShellResult:
    """Outcome of one shell-mode command execution.

    Attributes:
        command: The exact string submitted to the shell.
        returncode: Exit status from the subprocess.
        stdout: Captured standard output (empty string when the process
            wrote nothing).
        stderr: Captured standard error.
    """

    command: str
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        """``True`` iff the command exited cleanly (returncode 0)."""
        return self.returncode == 0


@dataclass
class ShellModeManager:
    """Track the REPL's shell-mode state across user inputs.

    The manager owns:

    * the current mode (``agent`` or ``shell``),
    * the prompt prefix to render in each mode,
    * a bounded shared history of every line the user submitted (across
      modes, in submission order).

    Both the agent and shell prompts are configurable so a downstream
    UI can swap colours / banners without subclassing the manager.

    Attributes:
        mode: Current operating mode. Changed via :meth:`toggle` or
            :meth:`set_mode`.
        agent_prompt: Prompt prefix rendered in agent mode.
        shell_prompt: Prompt prefix rendered in shell mode.
        history: Bounded deque of ``(mode, line)`` tuples in submission
            order (newest at the right).
        history_cap: Maximum history length; older entries are dropped.
    """

    mode: str = MODE_AGENT
    agent_prompt: str = _DEFAULT_AGENT_PROMPT
    shell_prompt: str = _DEFAULT_SHELL_PROMPT
    history_cap: int = _DEFAULT_HISTORY_CAP
    history: Deque[tuple[str, str]] = field(default_factory=deque)

    def __post_init__(self) -> None:
        """Validate the initial mode and trim history to the configured cap."""
        if self.mode not in _VALID_MODES:
            raise ValueError(
                f"unknown mode {self.mode!r} "
                f"(supported: {', '.join(_VALID_MODES)})"
            )
        # WHY: deque needs ``maxlen`` set on construction; defaulting via
        # field(default_factory=deque) lands a bare deque with no cap, so
        # we re-wrap once we know the cap.
        if self.history_cap <= 0:
            raise ValueError("history_cap must be positive")
        # Re-build with the maxlen so older entries are evicted on push.
        self.history = deque(self.history, maxlen=self.history_cap)

    # ------------------------------------------------------------------
    # Mode transitions
    # ------------------------------------------------------------------

    def toggle(self) -> str:
        """Flip between agent and shell mode and return the new mode."""
        self.mode = MODE_SHELL if self.mode == MODE_AGENT else MODE_AGENT
        return self.mode

    def set_mode(self, mode: str) -> None:
        """Set the current mode explicitly.

        Args:
            mode: ``"agent"`` or ``"shell"``.

        Raises:
            ValueError: When ``mode`` is not one of the supported values.
        """
        if mode not in _VALID_MODES:
            raise ValueError(
                f"unknown mode {mode!r} "
                f"(supported: {', '.join(_VALID_MODES)})"
            )
        self.mode = mode

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    @property
    def prompt(self) -> str:
        """Return the prompt prefix for the current mode."""
        return self.shell_prompt if self.mode == MODE_SHELL else self.agent_prompt

    def is_shell_mode(self) -> bool:
        """``True`` iff currently in shell mode."""
        return self.mode == MODE_SHELL

    def is_agent_mode(self) -> bool:
        """``True`` iff currently in agent mode."""
        return self.mode == MODE_AGENT

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def record(self, line: str) -> None:
        """Append ``line`` to history, tagged with the current mode.

        Empty lines are ignored so users mashing enter don't push noise.
        """
        if not line:
            return
        self.history.append((self.mode, line))

    def recent(self, n: int = 10) -> list[tuple[str, str]]:
        """Return the ``n`` most recently recorded lines (newest last).

        Args:
            n: Maximum number of entries to return.

        Returns:
            List of ``(mode, line)`` tuples. May be shorter than ``n``
            when history is sparse.
        """
        if n <= 0:
            return []
        if n >= len(self.history):
            return list(self.history)
        return list(self.history)[-n:]

    # ------------------------------------------------------------------
    # Shell execution
    # ------------------------------------------------------------------

    def run_shell(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        capture: bool = True,
    ) -> ShellResult:
        """Execute ``command`` via ``bash -c`` and return a :class:`ShellResult`.

        This is the core of "every input runs as ``bash -c <input>``"
        from the stoat shell-mode promise. Only used when the manager is
        in shell mode; the caller is responsible for the mode check (so
        agent-mode REPLs can still inject ``run_shell`` for ``!``-prefixed
        escapes if desired).

        Args:
            command: Shell command line. Empty strings short-circuit to
                a no-op success.
            cwd: Working directory for the subprocess. Defaults to the
                current process cwd.
            timeout: Optional seconds before raising
                :class:`subprocess.TimeoutExpired`. ``None`` waits forever.
            capture: When ``True`` (default), stdout/stderr are captured
                and returned in the result. When ``False``, the child
                inherits the parent's streams (interactive UX).

        Returns:
            :class:`ShellResult` with the command, return code, and (when
            ``capture`` is set) the captured stdout/stderr.
        """
        # WHY: an empty command is a common shell habit — pressing enter
        # in a shell does nothing. We mirror that rather than spawning
        # an empty process or raising.
        cmd = command.strip()
        if not cmd:
            return ShellResult(command="", returncode=0, stdout="", stderr="")

        # Validate that bash is shellable (the shlex.quote round-trip
        # also catches null bytes and other oddities that ``bash -c``
        # would silently mishandle).
        try:
            shlex.quote(cmd)
        except Exception as exc:  # noqa: BLE001 — defensive
            return ShellResult(
                command=cmd,
                returncode=2,
                stdout="",
                stderr=f"stoat shell: invalid command ({exc})",
            )

        # WHY: we always go through ``bash -c`` (not ``shell=True`` on
        # the joined string) so users get bash semantics regardless of
        # the parent shell. ``$BASH`` is honored when set so containers
        # / nix shells can override the binary path.
        bash = os.environ.get("BASH") or "bash"
        try:
            proc = subprocess.run(
                [bash, "-c", cmd],
                cwd=cwd,
                capture_output=capture,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            return ShellResult(
                command=cmd,
                returncode=127,
                stdout="",
                stderr=f"stoat shell: bash not found ({exc})",
            )
        except subprocess.TimeoutExpired as exc:
            return ShellResult(
                command=cmd,
                returncode=124,
                stdout="",
                stderr=f"stoat shell: timeout after {exc.timeout}s",
            )
        except Exception as exc:  # noqa: BLE001 — never crash the REPL
            return ShellResult(
                command=cmd,
                returncode=1,
                stdout="",
                stderr=f"stoat shell: {exc}",
            )

        return ShellResult(
            command=cmd,
            returncode=int(proc.returncode),
            stdout=proc.stdout if capture else "",
            stderr=proc.stderr if capture else "",
        )
