"""IPython REPL tool — stateful Python REPL backed by a subprocess.

A persistent IPython (or ``python -i``) subprocess is held open so that
variables, imports, and state survive across tool calls. This is the
shape used by the SWE-bench / agent literature for "scratch-pad
Python" — the agent can introspect, instrument, and re-test fixes
without paying the import cost on every turn.

This module was originally landed by M6 as a scaffold using a
blocking ``readline`` loop. M12 (this file) hardens it:

* The reader is non-blocking — stdout is set to ``O_NONBLOCK`` and
  drained through a :class:`selectors.DefaultSelector`. A user
  ``time.sleep(60)`` no longer pins the reader thread; we simply
  observe no output for ``hang_timeout`` seconds and bail.
* :meth:`IPythonSession.restart` and the implicit hang-recovery path
  send ``SIGTERM`` first, wait up to 5 s, and only escalate to
  ``SIGKILL`` if the kernel is still alive (per the global
  graceful-shutdown rule).
* :meth:`IPythonTool.health` runs a ``pass`` round-trip so callers can
  probe a kernel without committing user code to a possibly broken
  REPL.

It avoids any IPython-specific package dependency by falling back to
``python -i -u`` when the ``ipython`` binary is not on PATH.
"""
from __future__ import annotations

import logging
import os
import selectors
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

# fcntl is POSIX-only. On Windows we silently fall back to the blocking
# readline path (Windows is not a SWE-bench target).
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX
    _fcntl = None  # type: ignore[assignment]

_LOG = logging.getLogger(__name__)

# Sentinel printed after every command so we know where the output ends.
# Random-ish suffix keeps it from colliding with user code.
_END_SENTINEL = "__CHIMERA_IPY_END_3f7a91__"

# Default read timeout for a single ``run`` call (the whole command,
# not per chunk). Used as a hard ceiling when the caller does not
# override it.
_DEFAULT_READ_TIMEOUT = 30.0

# If we go this long without observing *any* new bytes from the kernel
# while a command is in flight, we treat the kernel as hung and
# restart it. Smaller than ``_DEFAULT_READ_TIMEOUT`` on purpose so the
# tool surfaces a useful error instead of silently waiting.
_DEFAULT_HANG_TIMEOUT = 30.0

# How long ``stop`` / ``restart`` will wait for SIGTERM to take effect
# before escalating to SIGKILL.
_TERM_GRACE_SECONDS = 5.0

# Cap output to keep prompt token counts in check — same default as
# BaseTool.max_result_size_chars but enforced inside the tool so the
# subprocess can't flood the wire.
_MAX_OUTPUT_CHARS = 30_000


class IPythonSession:
    """A long-lived ``ipython`` / ``python -i`` subprocess.

    Thread-safe (a single ``threading.Lock`` serialises ``run`` calls)
    so the same session can be shared by parallel tool callers without
    interleaving stdout.

    Hardening notes (M12):

    * stdout is configured non-blocking so a stuck command cannot pin
      the reader thread. Reads go through ``selectors.DefaultSelector``.
    * ``hang_timeout`` triggers a graceful restart (SIGTERM, then
      SIGKILL only if the kernel ignores SIGTERM for ``_TERM_GRACE_SECONDS``).
    * Restarts emit a structured log record at WARNING level so harness
      operators can correlate hangs with kernel cycles.
    """

    def __init__(
        self,
        cwd: str | None = None,
        executable: str | None = None,
        hang_timeout: float = _DEFAULT_HANG_TIMEOUT,
    ) -> None:
        self._cwd = cwd
        self._executable = executable or self._detect_executable()
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._hang_timeout = hang_timeout
        self._restart_count = 0

    # ------------------------------------------------------------------
    # process lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_executable() -> list[str]:
        """Pick ``ipython --no-banner`` if available, else ``python -i``."""
        ipy = shutil.which("ipython")
        if ipy:
            return [ipy, "--no-banner", "--simple-prompt", "--no-confirm-exit"]
        # Fallback: stock interactive Python. ``-u`` disables stdout
        # buffering so we see output promptly.
        py = sys.executable or "python"
        return [py, "-i", "-u"]

    def start(self) -> None:
        """Spawn the REPL process if it is not already running."""
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
            bufsize=0,
        )
        # Switch stdout into non-blocking mode so the selectors-based
        # reader can spin without ever blocking on a partial line.
        if _fcntl is not None and self._proc.stdout is not None:
            try:
                fd = self._proc.stdout.fileno()
                flags = _fcntl.fcntl(fd, _fcntl.F_GETFL)
                _fcntl.fcntl(fd, _fcntl.F_SETFL, flags | os.O_NONBLOCK)
            except (OSError, ValueError):  # pragma: no cover - rare
                pass

    def stop(self) -> None:
        """Terminate the REPL gracefully (SIGTERM, then SIGKILL only if needed)."""
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                try:
                    proc.stdin.write("exit()\n")
                    proc.stdin.flush()
                except (BrokenPipeError, ValueError, OSError):
                    pass
            self._terminate_gracefully(proc)
        finally:
            for stream in (proc.stdin, proc.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:  # noqa: BLE001 - best-effort close
                    pass

    def restart(self) -> None:
        """Tear down the kernel and start a fresh one.

        Sends ``SIGTERM`` and waits up to ``_TERM_GRACE_SECONDS`` for
        the process to exit. Only escalates to ``SIGKILL`` if the
        kernel is still alive after that window (per the project-wide
        graceful-shutdown rule). The restart is logged.
        """
        with self._lock:
            self._restart_locked(reason="explicit restart")

    def _restart_locked(self, reason: str) -> None:
        """Caller must hold ``self._lock``."""
        old = self._proc
        self._proc = None
        if old is not None:
            self._terminate_gracefully(old)
            for stream in (old.stdin, old.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:  # noqa: BLE001
                    pass
        self._restart_count += 1
        _LOG.warning(
            "ipython.kernel.restart",
            extra={
                "reason": reason,
                "restart_count": self._restart_count,
                "hang_timeout": self._hang_timeout,
            },
        )
        # Lazy: the next ``run`` will call ``start``. We don't eagerly
        # respawn here so callers that just wanted a clean teardown
        # can ``stop`` after a restart.

    def _terminate_gracefully(self, proc: subprocess.Popen[str]) -> None:
        """Send SIGTERM, wait, escalate to SIGKILL only if needed."""
        if proc.poll() is not None:
            return
        try:
            proc.terminate()  # SIGTERM on POSIX, TerminateProcess on Windows
        except (ProcessLookupError, OSError):
            return
        try:
            proc.wait(timeout=_TERM_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            pass
        # SIGTERM did not take. Escalate.
        _LOG.warning(
            "ipython.kernel.sigkill_escalation",
            extra={"pid": proc.pid, "grace_seconds": _TERM_GRACE_SECONDS},
        )
        try:
            if hasattr(signal, "SIGKILL"):
                proc.send_signal(signal.SIGKILL)
            else:  # pragma: no cover - Windows
                proc.kill()
        except (ProcessLookupError, OSError):
            return
        try:
            proc.wait(timeout=_TERM_GRACE_SECONDS)
        except subprocess.TimeoutExpired:  # pragma: no cover - very rare
            pass

    # ------------------------------------------------------------------
    # exec
    # ------------------------------------------------------------------

    @property
    def restart_count(self) -> int:
        """Number of times this session has been torn down and respawned."""
        return self._restart_count

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def run(
        self,
        code: str,
        timeout: float = _DEFAULT_READ_TIMEOUT,
        hang_timeout: float | None = None,
    ) -> tuple[str, bool]:
        """Execute ``code`` in the live REPL.

        Args:
            code: Python source to execute. Multi-line input is fine —
                the sentinel is printed *after* the user's last line so
                it terminates against the next prompt.
            timeout: Hard ceiling on the whole command. The reader
                returns ``(partial_output, False)`` when this expires.
            hang_timeout: Per-call override of the no-progress timer.
                Defaults to the session's ``hang_timeout``.

        Returns:
            ``(output, ok)``. ``ok`` is ``False`` if the subprocess
            died, the read timed out, no sentinel ever appeared, or
            the kernel hung and was restarted mid-command.
        """
        hang_t = self._hang_timeout if hang_timeout is None else hang_timeout
        with self._lock:
            self.start()
            assert self._proc is not None
            assert self._proc.stdin is not None
            assert self._proc.stdout is not None

            # A trailing blank line is required so ``python -i`` closes
            # any pending compound statement (def/class/if) before our
            # sentinel print runs. ``ipython`` tolerates the extra
            # newline harmlessly.
            payload = code.rstrip("\n") + "\n\n" + f"print({_END_SENTINEL!r})\n"
            try:
                self._proc.stdin.write(payload)
                self._proc.stdin.flush()
            except (BrokenPipeError, ValueError, OSError) as exc:
                # Kernel is gone. Restart so the next call works.
                self._restart_locked(reason=f"stdin closed: {exc}")
                return f"REPL stdin closed: {exc}", False

            return self._read_until_sentinel(timeout=timeout, hang_timeout=hang_t)

    def _read_until_sentinel(
        self,
        timeout: float,
        hang_timeout: float,
    ) -> tuple[str, bool]:
        """Drain stdout until the sentinel arrives, the deadline passes,
        or we observe no output for ``hang_timeout`` seconds.

        Caller must hold ``self._lock`` and have already asserted that
        ``self._proc`` is alive.
        """
        proc = self._proc
        assert proc is not None
        assert proc.stdout is not None

        deadline = time.monotonic() + timeout
        last_progress = time.monotonic()
        buf: list[str] = []
        leftover = ""
        total = 0
        truncated = False

        sel: selectors.BaseSelector | None = None
        use_selectors = _fcntl is not None
        if use_selectors:
            sel = selectors.DefaultSelector()
            try:
                sel.register(proc.stdout, selectors.EVENT_READ)
            except (ValueError, KeyError):  # pragma: no cover - rare
                use_selectors = False
                sel = None

        try:
            while True:
                now = time.monotonic()
                if now >= deadline:
                    return self._finalise(buf, leftover, ok=False, truncated=truncated)
                if proc.poll() is not None:
                    return self._finalise(buf, leftover, ok=False, truncated=truncated)
                if now - last_progress > hang_timeout:
                    # Hang. Restart the kernel, log it, and surface a
                    # clean failure.
                    self._restart_locked(
                        reason=f"no output for {hang_timeout:.1f}s",
                    )
                    msg = (
                        f"\n[ipython kernel hung — no output for "
                        f"{hang_timeout:.1f}s; kernel restarted]\n"
                    )
                    buf.append(msg)
                    return self._finalise(buf, leftover, ok=False, truncated=truncated)

                slice_budget = min(deadline - now, hang_timeout - (now - last_progress))
                slice_budget = max(0.05, min(slice_budget, 1.0))

                chunk = self._read_chunk(sel, proc, slice_budget, use_selectors)
                if chunk is None:
                    # No data available right now; loop and re-check
                    # deadlines.
                    continue
                if chunk == "":
                    # EOF — kernel died.
                    return self._finalise(buf, leftover, ok=False, truncated=truncated)

                last_progress = time.monotonic()
                leftover += chunk

                # Process complete lines for sentinel detection.
                while "\n" in leftover:
                    line, leftover = leftover.split("\n", 1)
                    line = line + "\n"
                    if _END_SENTINEL in line:
                        return self._finalise(
                            buf, leftover, ok=True, truncated=truncated
                        )
                    if total >= _MAX_OUTPUT_CHARS:
                        truncated = True
                        # Keep draining for the sentinel but stop
                        # appending to ``buf``.
                        continue
                    buf.append(line)
                    total += len(line)

                # Sentinel could also appear inside the still-buffered
                # tail (rare — sentinel ends in ``\n`` from print so
                # this is mostly defensive).
                if _END_SENTINEL in leftover:
                    pre, _, post = leftover.partition(_END_SENTINEL)
                    if pre and total < _MAX_OUTPUT_CHARS:
                        buf.append(pre)
                    leftover = post
                    return self._finalise(buf, leftover, ok=True, truncated=truncated)
        finally:
            if sel is not None:
                try:
                    sel.unregister(proc.stdout)
                except (KeyError, ValueError):
                    pass
                sel.close()

    @staticmethod
    def _read_chunk(
        sel: selectors.BaseSelector | None,
        proc: subprocess.Popen[str],
        slice_budget: float,
        use_selectors: bool,
    ) -> str | None:
        """Return up to one chunk of stdout, or ``None`` if nothing was ready.

        Returns ``""`` to signal EOF.
        """
        stdout = proc.stdout
        assert stdout is not None
        if use_selectors and sel is not None:
            events = sel.select(timeout=slice_budget)
            if not events:
                return None
            # Read directly from the underlying fd so we sidestep
            # ``io.TextIOWrapper``'s "read returned None" assertion
            # (which fires on a non-blocking pipe with no data ready
            # despite ``select`` having flagged the fd).
            try:
                fd = stdout.fileno()
                raw = os.read(fd, 4096)
            except (BlockingIOError, OSError):
                return None
            if raw == b"":
                return ""
            try:
                return raw.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - never crash on decoding
                return raw.decode("latin-1", errors="replace")
        # Fallback path (e.g. Windows or fcntl unavailable). Use the
        # blocking ``readline`` but cap progress per call by polling
        # ``poll`` between reads.
        time.sleep(min(0.05, slice_budget))
        try:
            line = stdout.readline()
        except Exception:  # noqa: BLE001
            return ""
        return line if line else ""

    def _finalise(
        self,
        buf: list[str],
        leftover: str,
        ok: bool,
        truncated: bool,
    ) -> tuple[str, bool]:
        if truncated:
            buf.append(f"\n[output truncated at {_MAX_OUTPUT_CHARS} chars]\n")
        # Ignore any trailing partial line — it never terminated and
        # cannot be parsed cleanly.
        del leftover
        return "".join(buf), ok

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------

    def health(self, timeout: float = 5.0) -> tuple[bool, str]:
        """Run a no-op round-trip and confirm the kernel responds cleanly.

        Sends ``pass`` and waits for the sentinel. Returns
        ``(ok, detail)`` where ``detail`` is human-readable diagnostic
        text suitable for surfacing to the agent or operator.
        """
        out, ok = self.run("pass", timeout=timeout, hang_timeout=timeout)
        if not ok:
            return False, f"health check failed: {out!r}"
        if out.strip():
            # ``pass`` should produce no user-visible output. Any
            # leftover suggests a stale prompt or noise.
            return True, f"healthy (with stray output: {out!r})"
        return True, "healthy"


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
        hang_timeout: float = _DEFAULT_HANG_TIMEOUT,
    ) -> None:
        self._cwd = cwd
        self._session = session
        self._hang_timeout = hang_timeout

    def _ensure_session(self, env: Environment | None) -> IPythonSession:
        if self._session is not None:
            return self._session
        cwd = self._cwd
        if cwd is None and env is not None:
            cwd = getattr(env, "workdir", None) or getattr(env, "cwd", None)
        self._session = IPythonSession(cwd=cwd, hang_timeout=self._hang_timeout)
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
                metadata={"restart_count": session.restart_count},
            )
        return ToolResult(
            output=output,
            metadata={"restart_count": session.restart_count},
        )

    def health(self, env: Environment | None = None) -> ToolResult:
        """Probe the REPL with a ``pass`` round-trip."""
        session = self._ensure_session(env)
        ok, detail = session.health()
        if not ok:
            return ToolResult(
                output=detail,
                error="ipython health probe failed",
                metadata={"restart_count": session.restart_count},
            )
        return ToolResult(
            output=detail,
            metadata={"restart_count": session.restart_count},
        )

    def restart(self) -> None:
        """Force a graceful kernel restart."""
        if self._session is not None:
            self._session.restart()

    def shutdown(self) -> None:
        """Terminate the underlying REPL gracefully."""
        if self._session is not None:
            self._session.stop()
            self._session = None


def ipython_health(tool: IPythonTool, env: Environment | None = None) -> ToolResult:
    """Module-level convenience wrapper around :meth:`IPythonTool.health`."""
    return tool.health(env)


__all__ = ["IPythonTool", "IPythonSession", "ipython_health"]
