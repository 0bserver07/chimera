"""IPython REPL tool — stateful Python REPL backed by a subprocess.

A persistent IPython (or `python -i`) subprocess is held open so that
variables, imports, and state survive across tool calls. This is the
shape used by the SWE-bench / agent literature for "scratch-pad
Python" — the agent can introspect, instrument, and re-test fixes
without paying the import cost on every turn.

The implementation here is a deliberately small scaffold: it gives the
agent a working REPL today and leaves headroom for a future hardening
pass (better prompt detection, output truncation strategies, kernel
restart on hang). It avoids any IPython-specific package dependency by
falling back to ``python -i -u`` when the ``ipython`` binary is not on
PATH.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

# Sentinel printed after every command so we know where the output ends.
# Random-ish suffix keeps it from colliding with user code.
_END_SENTINEL = "__CHIMERA_IPY_END_3f7a91__"

# Default time the reader thread waits for the sentinel before giving
# up and returning whatever it has captured so far.
_DEFAULT_READ_TIMEOUT = 30.0

# Cap output to keep prompt token counts in check — same default as
# BaseTool.max_result_size_chars but enforced inside the tool so the
# subprocess can't flood the wire.
_MAX_OUTPUT_CHARS = 30_000


class IPythonSession:
    """A long-lived ``ipython`` / ``python -i`` subprocess.

    Thread-safe (a single ``threading.Lock`` serialises ``run`` calls)
    so the same session can be shared by parallel tool callers without
    interleaving stdout.
    """

    def __init__(
        self,
        cwd: str | None = None,
        executable: str | None = None,
    ) -> None:
        self._cwd = cwd
        self._executable = executable or self._detect_executable()
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _detect_executable() -> list[str]:
        """Pick ``ipython --no-banner`` if available, else ``python -i``."""
        ipy = shutil.which("ipython")
        if ipy:
            return [ipy, "--no-banner", "--simple-prompt", "--no-confirm-exit"]
        # Fallback: stock interactive Python. ``-u`` disables stdout
        # buffering so we see output promptly.
        return ["python", "-i", "-u"]

    def start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        env = os.environ.copy()
        # Force unbuffered output even when we fall through to ipython.
        env.setdefault("PYTHONUNBUFFERED", "1")
        # Disable readline-driven prompt echoing where we can.
        env.setdefault("TERM", "dumb")
        self._proc = subprocess.Popen(  # noqa: S603 - executable is curated
            self._executable,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=self._cwd,
            env=env,
            text=True,
            bufsize=1,
        )

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                try:
                    proc.stdin.write("exit()\n")
                    proc.stdin.flush()
                except (BrokenPipeError, ValueError):
                    pass
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    # Last-resort termination only after graceful path.
                    proc.kill()
        finally:
            for stream in (proc.stdin, proc.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass

    def run(
        self,
        code: str,
        timeout: float = _DEFAULT_READ_TIMEOUT,
    ) -> tuple[str, bool]:
        """Execute ``code`` in the live REPL.

        Returns ``(output, ok)``. ``ok`` is ``False`` if the subprocess
        died, the read timed out, or no sentinel ever appeared.
        """
        with self._lock:
            self.start()
            assert self._proc is not None
            assert self._proc.stdin is not None
            assert self._proc.stdout is not None

            # Wrap the user code so we can detect end-of-output. We
            # write the code, then a print of our sentinel. Even if the
            # user code raised, the REPL will continue and emit the
            # sentinel print on the next prompt.
            payload = code.rstrip("\n") + "\n" + f"print({_END_SENTINEL!r})\n"
            try:
                self._proc.stdin.write(payload)
                self._proc.stdin.flush()
            except (BrokenPipeError, ValueError) as exc:
                return f"REPL stdin closed: {exc}", False

            buf: list[str] = []
            deadline = time.monotonic() + timeout
            ok = False
            total = 0
            while time.monotonic() < deadline:
                line = self._readline_with_timeout(deadline)
                if line is None:
                    break
                if _END_SENTINEL in line:
                    ok = True
                    break
                buf.append(line)
                total += len(line)
                if total > _MAX_OUTPUT_CHARS:
                    buf.append(
                        f"\n[output truncated at {_MAX_OUTPUT_CHARS} chars]\n"
                    )
                    # Drain to sentinel to keep the REPL aligned.
                    while time.monotonic() < deadline:
                        drain = self._readline_with_timeout(deadline)
                        if drain is None or _END_SENTINEL in drain:
                            ok = drain is not None
                            break
                    break
            return "".join(buf), ok

    def _readline_with_timeout(self, deadline: float) -> str | None:
        """Best-effort line read that respects ``deadline``."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return None
        # subprocess.Popen.stdout.readline is blocking; we approximate
        # a deadline by checking poll() first. This is not perfect but
        # is good enough for a scaffold — a future iteration should use
        # a selectors-based reader thread.
        if time.monotonic() >= deadline:
            return None
        if proc.poll() is not None:
            return None
        try:
            return proc.stdout.readline()
        except Exception:
            return None


class IPythonTool(BaseTool):
    """Tool exposing a stateful Python REPL to the agent.

    Each tool instance owns one :class:`IPythonSession`. The session is
    started lazily on first ``execute`` and lives for the tool's
    lifetime. Callers that want a fresh kernel per task should
    construct a fresh ``IPythonTool`` per task.
    """

    name = "ipython"
    description = (
        "Execute Python code in a stateful IPython REPL. Variables, "
        "imports, and state persist across calls. Use this for "
        "exploratory debugging, instrumenting code, and verifying "
        "patches without restarting the interpreter."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute.",
            },
            "timeout": {
                "type": "number",
                "description": "Read timeout in seconds.",
                "default": _DEFAULT_READ_TIMEOUT,
            },
        },
        "required": ["code"],
    }

    is_concurrency_safe = False  # one REPL, sequential calls
    is_read_only = False
    is_destructive = False

    def __init__(
        self,
        cwd: str | None = None,
        session: IPythonSession | None = None,
    ) -> None:
        self._cwd = cwd
        self._session = session

    def _ensure_session(self, env: Environment | None) -> IPythonSession:
        if self._session is not None:
            return self._session
        cwd = self._cwd
        if cwd is None and env is not None:
            cwd = getattr(env, "workdir", None) or getattr(env, "cwd", None)
        self._session = IPythonSession(cwd=cwd)
        return self._session

    def execute(
        self,
        args: dict[str, Any],
        env: Environment | None,
    ) -> ToolResult:
        code = args.get("code", "")
        if not isinstance(code, str) or not code.strip():
            return ToolResult(output="", error="missing 'code' argument")
        timeout = float(args.get("timeout", _DEFAULT_READ_TIMEOUT))
        session = self._ensure_session(env)
        output, ok = session.run(code, timeout=timeout)
        if not ok:
            return ToolResult(
                output=output,
                error="ipython REPL timed out or ended unexpectedly",
            )
        return ToolResult(output=output)

    def shutdown(self) -> None:
        """Terminate the underlying REPL gracefully."""
        if self._session is not None:
            self._session.stop()
            self._session = None


__all__ = ["IPythonTool", "IPythonSession"]
