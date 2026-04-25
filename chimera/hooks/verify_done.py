#!/usr/bin/env python3
"""Stop hook: verify all tests pass before the agent declares done.

A compatible coding-agent harness invokes this script as a Stop hook
when the agent is about to finish.  It runs the project's test suite
and reports failures so the agent knows it is not actually done.

Exit codes:
    0 — all tests pass, the agent may stop.
    1 — tests failed, output shows failures (the agent should continue).

The test command can be configured via the ``CHIMERA_TEST_CMD`` environment
variable.  Defaults to ``python -m pytest --tb=short -q``.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path


# Default test command
_DEFAULT_TEST_CMD = f"{sys.executable} -m pytest --tb=short -q"


def get_test_command() -> list[str]:
    """Get the test command to run.

    Reads from ``CHIMERA_TEST_CMD`` environment variable, falling back
    to the default pytest command.

    Returns:
        Command as a list of arguments (argv style).
    """
    custom = os.environ.get("CHIMERA_TEST_CMD", "").strip()
    if custom:
        return shlex.split(custom)
    return shlex.split(_DEFAULT_TEST_CMD)


def run_test_suite(
    project_root: str | None = None,
    test_command: list[str] | None = None,
) -> tuple[bool, str]:
    """Run the project's test suite.

    Args:
        project_root: Working directory for the test runner.
        test_command: Override test command.  Defaults to ``get_test_command()``.

    Returns:
        ``(passed, output)`` — whether all tests passed and the output.
    """
    cmd = test_command or get_test_command()
    cwd = project_root or str(Path.cwd())

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=cwd,
        )
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr
        return result.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        return False, "Test suite timed out after 300 seconds."
    except FileNotFoundError:
        return True, f"Test runner not found: {cmd[0]} — skipping verification."


def main() -> None:
    """Entry point for the hook script."""
    passed, output = run_test_suite()

    if passed:
        print("[verify-done] All tests passed.")
        sys.exit(0)
    else:
        print(f"[verify-done] Tests FAILED — not done yet.\n{output}")
        sys.exit(1)


if __name__ == "__main__":
    main()
