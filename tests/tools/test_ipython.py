"""Tests for the hardened IPython REPL tool.

The session under test is pinned to ``python -i -u`` (instead of the
auto-detected ``ipython`` binary) so output is deterministic across
machines that may or may not have IPython installed and so the
banner / prompt strings stay stable across versions.
"""
from __future__ import annotations

import logging
import os
import sys
import time

import pytest

from chimera.tools.ipython import (
    IPythonSession,
    IPythonTool,
    ipython_health,
)

# Pinned interpreter — never auto-detect ``ipython`` for these tests.
_PY = [sys.executable, "-i", "-u"]


def _new_session(hang_timeout: float = 30.0) -> IPythonSession:
    return IPythonSession(executable=list(_PY), hang_timeout=hang_timeout)


# ---------------------------------------------------------------------------
# normal exec
# ---------------------------------------------------------------------------


class TestNormalExecution:
    def test_simple_print(self) -> None:
        s = _new_session()
        try:
            out, ok = s.run("print(2+2)")
            assert ok is True
            assert "4" in out
        finally:
            s.stop()

    def test_state_persists_across_calls(self) -> None:
        s = _new_session()
        try:
            _, ok1 = s.run("answer = 42")
            assert ok1
            out, ok2 = s.run("print(answer)")
            assert ok2
            assert "42" in out
        finally:
            s.stop()

    def test_multi_line_code(self) -> None:
        s = _new_session()
        try:
            code = "x = 7\n" "y = 5\n" "print(x * y)\n"
            out, ok = s.run(code)
            assert ok
            assert "35" in out
        finally:
            s.stop()

    def test_multi_line_def_and_call(self) -> None:
        s = _new_session()
        try:
            out, ok = s.run("def add(a, b):\n    return a + b\n")
            assert ok, f"def block did not terminate: {out!r}"
            out, ok = s.run("print(add(11, 31))")
            assert ok
            assert "42" in out
        finally:
            s.stop()

    def test_exception_does_not_break_repl(self) -> None:
        s = _new_session()
        try:
            out_err, ok_err = s.run("1/0")
            assert ok_err  # sentinel still arrives
            assert "ZeroDivisionError" in out_err
            out_ok, ok2 = s.run("print('still alive')")
            assert ok2
            assert "still alive" in out_ok
        finally:
            s.stop()


# ---------------------------------------------------------------------------
# hang detection + restart
# ---------------------------------------------------------------------------


class TestHangAndRestart:
    def test_hang_triggers_restart_and_logs(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        s = _new_session(hang_timeout=1.5)
        try:
            with caplog.at_level(logging.WARNING, logger="chimera.tools.ipython"):
                t0 = time.monotonic()
                out, ok = s.run(
                    "import time; time.sleep(60)",
                    timeout=10.0,
                    hang_timeout=1.5,
                )
                elapsed = time.monotonic() - t0
            assert ok is False
            # We should have bailed promptly — well under the 60s sleep.
            assert elapsed < 6.0, f"hang detection too slow: {elapsed}s"
            assert "kernel hung" in out
            assert s.restart_count == 1
            messages = [r.getMessage() for r in caplog.records]
            assert any("ipython.kernel.restart" in m for m in messages)
        finally:
            s.stop()

    def test_repl_recovers_after_hang(self) -> None:
        s = _new_session(hang_timeout=1.0)
        try:
            _, ok = s.run("import time; time.sleep(60)", timeout=5.0)
            assert ok is False
            assert s.restart_count == 1
            # Next call must boot a fresh kernel and work.
            out, ok2 = s.run("print('back')", timeout=10.0)
            assert ok2
            assert "back" in out
        finally:
            s.stop()

    def test_explicit_restart_increments_counter(self) -> None:
        s = _new_session()
        try:
            s.start()
            assert s.is_alive
            s.restart()
            assert s.restart_count == 1
            # Next ``run`` lazily respawns the kernel.
            out, ok = s.run("print('hi')")
            assert ok
            assert "hi" in out
        finally:
            s.stop()

    def test_restart_uses_sigterm_first(self) -> None:
        """Graceful shutdown: the kernel should exit on SIGTERM, not SIGKILL.

        We can't directly observe which signal was sent, but a healthy
        Python REPL will exit cleanly on SIGTERM, so ``poll()`` should
        return a non-None value within the grace window.
        """
        s = _new_session()
        try:
            s.start()
            proc = s._proc
            assert proc is not None
            pid = proc.pid
            s.restart()
            # The old process must be gone (cleanly).
            assert _pid_dead(pid), "old kernel did not exit on SIGTERM"
        finally:
            s.stop()


def _pid_dead(pid: int) -> bool:
    """Return True if ``pid`` is no longer running. Best-effort, POSIX."""
    if not hasattr(os, "kill"):  # pragma: no cover - non-POSIX
        return True
    # Give the OS a moment to reap.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:  # pragma: no cover
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
# health probe
# ---------------------------------------------------------------------------


class TestHealthProbe:
    def test_health_passes_on_fresh_kernel(self) -> None:
        s = _new_session()
        try:
            ok, detail = s.health()
            assert ok is True
            assert "healthy" in detail
        finally:
            s.stop()

    def test_health_passes_after_real_work(self) -> None:
        s = _new_session()
        try:
            s.run("xs = list(range(3))")
            ok, _ = s.health()
            assert ok is True
        finally:
            s.stop()

    def test_health_fails_on_dead_kernel(self) -> None:
        """If we shut the kernel down, the next ``run`` lazy-respawns
        it — so health must succeed. To exercise the failure path we
        force a hang while probing.
        """
        s = _new_session(hang_timeout=0.5)
        try:
            # Make the kernel busy with an infinite sleep BEFORE we
            # probe; the next call is the health probe and must time
            # out, restart the kernel, and report failure.
            # We have to inject the hang via ``run`` first so the
            # subsequent health call observes it.
            # However ``run`` is serialised by the lock — instead, set
            # a tiny hang_timeout and probe a kernel that has nothing
            # wrong. The probe should pass quickly. So instead force
            # failure by closing stdin underneath.
            s.start()
            proc = s._proc
            assert proc is not None
            assert proc.stdin is not None
            proc.stdin.close()
            # Now health should fail because writing the ``pass``
            # payload will raise ValueError on a closed stdin and
            # ``run`` returns ``ok=False``.
            ok, detail = s.health(timeout=2.0)
            assert ok is False
            assert "failed" in detail
        finally:
            s.stop()


# ---------------------------------------------------------------------------
# tool wrapper
# ---------------------------------------------------------------------------


class TestIPythonTool:
    def test_execute_returns_output(self) -> None:
        sess = _new_session()
        tool = IPythonTool(session=sess)
        try:
            result = tool.execute({"code": "print(2+3)"}, env=None)
            assert result.error is None
            assert "5" in result.output
            assert result.metadata["restart_count"] == 0
        finally:
            tool.shutdown()

    def test_execute_rejects_empty_code(self) -> None:
        sess = _new_session()
        tool = IPythonTool(session=sess)
        try:
            result = tool.execute({"code": "   "}, env=None)
            assert result.error == "missing 'code' argument"
        finally:
            tool.shutdown()

    def test_health_passes_via_tool(self) -> None:
        sess = _new_session()
        tool = IPythonTool(session=sess)
        try:
            result = tool.health()
            assert result.error is None
            assert "healthy" in result.output
        finally:
            tool.shutdown()

    def test_module_level_health_helper(self) -> None:
        sess = _new_session()
        tool = IPythonTool(session=sess)
        try:
            result = ipython_health(tool)
            assert result.error is None
        finally:
            tool.shutdown()

    def test_restart_via_tool(self) -> None:
        sess = _new_session()
        tool = IPythonTool(session=sess)
        try:
            tool.execute({"code": "print(1)"}, env=None)
            assert sess.restart_count == 0
            tool.restart()
            assert sess.restart_count == 1
            # Next exec must still work.
            result = tool.execute({"code": "print('alive')"}, env=None)
            assert result.error is None
            assert "alive" in result.output
            assert result.metadata["restart_count"] == 1
        finally:
            tool.shutdown()

    def test_hang_surfaces_as_error(self) -> None:
        sess = _new_session(hang_timeout=1.0)
        tool = IPythonTool(session=sess)
        try:
            result = tool.execute(
                {"code": "import time; time.sleep(60)", "timeout": 5.0},
                env=None,
            )
            assert result.error is not None
            assert "timed out" in result.error or "ended unexpectedly" in result.error
            assert result.metadata["restart_count"] == 1
        finally:
            tool.shutdown()


# ---------------------------------------------------------------------------
# tool schema sanity (ride-along — keeps M6 contract intact)
# ---------------------------------------------------------------------------


class TestSchema:
    def test_tool_metadata(self) -> None:
        tool = IPythonTool()
        assert tool.name == "ipython"
        assert "code" in tool.parameters["properties"]
        assert tool.parameters["required"] == ["code"]
        assert tool.is_concurrency_safe is False
