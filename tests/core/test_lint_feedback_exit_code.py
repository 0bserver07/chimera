"""Regression: LintFeedbackLoop uses exit code, not output emptiness.

Ruff prints 'All checks passed!' + a 'No Python files found' warning while
exiting 0. The loop must treat that as success (empty errors), not feed it
back as a bogus fix task.
"""

from __future__ import annotations

from unittest import mock

from chimera.core.loops.lint_feedback import LintFeedbackLoop


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> mock.Mock:
    m = mock.Mock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def test_success_exit_returns_empty_even_with_output() -> None:
    loop = LintFeedbackLoop()

    class _Env:
        workdir = "."

    with mock.patch("subprocess.run", return_value=_proc(
        0, stdout="All checks passed!\n", stderr="warning: No Python files found\n"
    )):
        assert loop._run_linter(_Env()) == ""  # exit 0 → no errors


def test_nonzero_exit_returns_the_violations() -> None:
    loop = LintFeedbackLoop()

    class _Env:
        workdir = "."

    with mock.patch("subprocess.run", return_value=_proc(
        1, stdout="solution.py:1:1: F401 unused import\n"
    )):
        out = loop._run_linter(_Env())
        assert "F401" in out
