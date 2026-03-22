#!/usr/bin/env python3
"""PostToolUse hook: run related tests after file edits.

Claude Code calls this script as a PostToolUse hook after Write or Edit
tool calls.  It reads tool input as JSON on stdin (falling back to the
``TOOL_INPUT`` environment variable), extracts the modified file path,
finds related test files, and runs them with pytest.

Exit codes:
    0 — always (PostToolUse hooks inform only, they cannot block).

Output on stdout is relayed to Claude so it can see test results.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


# Tools that modify files and should trigger test runs.
_CHECKED_TOOLS = {"Write", "Edit", "write", "edit"}


def _read_input() -> dict:
    """Read tool input from stdin or TOOL_INPUT env var.

    Returns:
        Parsed JSON dict, or empty dict on failure.
    """
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                return json.loads(raw)
        except (json.JSONDecodeError, OSError):
            pass

    env_val = os.environ.get("TOOL_INPUT", "")
    if env_val:
        try:
            return json.loads(env_val)
        except json.JSONDecodeError:
            pass

    return {}


def find_test_files(file_path: str, project_root: str | None = None) -> list[str]:
    """Find test files related to the given source file.

    Searches using multiple strategies:
    1. Convention: ``foo.py`` -> ``tests/test_foo.py``
    2. Convention: ``pkg/foo.py`` -> ``tests/test_foo.py``
    3. Co-located: ``foo.py`` -> ``test_foo.py`` in same directory
    4. Search: look for ``test_*.py`` files containing the module name

    Args:
        file_path: Absolute or relative path to the modified file.
        project_root: Project root directory.  Defaults to cwd.

    Returns:
        List of absolute paths to related test files that exist on disk.
    """
    root = Path(project_root) if project_root else Path.cwd()
    source = Path(file_path)

    # Only look for tests for Python files
    if source.suffix != ".py":
        return []

    stem = source.stem
    # Skip files that are already tests
    if stem.startswith("test_"):
        return [str(source.resolve())] if source.resolve().exists() else []

    found: list[str] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        resolved = str(p.resolve())
        if resolved not in seen and p.exists():
            seen.add(resolved)
            found.append(resolved)

    # Strategy 1: tests/test_{stem}.py at project root
    _add(root / "tests" / f"test_{stem}.py")

    # Strategy 2: test_{stem}.py co-located with the source
    parent = source.parent if source.is_absolute() else root / source.parent
    _add(parent / f"test_{stem}.py")

    # Strategy 3: tests/ subdirectory relative to source
    _add(parent / "tests" / f"test_{stem}.py")

    # Strategy 4: search for test files mentioning the module
    tests_dir = root / "tests"
    if tests_dir.is_dir() and not found:
        for test_file in tests_dir.glob("test_*.py"):
            try:
                content = test_file.read_text(errors="replace")
                if stem in content:
                    _add(test_file)
            except OSError:
                continue

    return found


def run_tests(test_files: list[str], project_root: str | None = None) -> tuple[bool, str]:
    """Run pytest on the given test files.

    Args:
        test_files: List of absolute paths to test files.
        project_root: Working directory for pytest.

    Returns:
        ``(passed, output)`` — whether all tests passed and the combined output.
    """
    if not test_files:
        return True, "No related test files found."

    cmd = [
        sys.executable, "-m", "pytest",
        "--tb=short", "-q",
        *test_files,
    ]

    cwd = project_root or str(Path.cwd())

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=cwd,
        )
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr
        return result.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        return False, "Test run timed out after 120 seconds."
    except FileNotFoundError:
        return True, "pytest not found — skipping test run."


def handle(tool_input: dict, project_root: str | None = None) -> str:
    """Handle a PostToolUse event for auto-testing.

    Args:
        tool_input: Parsed tool input JSON.
        project_root: Project root directory.

    Returns:
        Output message for Claude.
    """
    tool_name = tool_input.get("tool_name", "")
    if tool_name not in _CHECKED_TOOLS:
        return ""

    params = tool_input.get("tool_input", tool_input)
    file_path = params.get("file_path", "") or params.get("path", "")

    if not file_path:
        return ""

    test_files = find_test_files(file_path, project_root)

    if not test_files:
        return f"[auto-test] No related tests found for {Path(file_path).name}"

    passed, output = run_tests(test_files, project_root)

    file_names = ", ".join(Path(f).name for f in test_files)
    if passed:
        header = f"[auto-test] PASSED — {file_names}"
    else:
        header = f"[auto-test] FAILED — {file_names}"

    return f"{header}\n{output}"


def main() -> None:
    """Entry point for the hook script."""
    tool_input = _read_input()

    if not tool_input:
        sys.exit(0)

    result = handle(tool_input)
    if result:
        print(result)

    # PostToolUse hooks always exit 0
    sys.exit(0)


if __name__ == "__main__":
    main()
