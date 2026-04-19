#!/usr/bin/env python3
"""PostToolUse hook: lint after file edits.

Claude Code calls this script as a PostToolUse hook after Write or Edit
tool calls.  It reads tool input as JSON on stdin (falling back to the
``TOOL_INPUT`` environment variable), extracts the modified file path,
and runs the configured linter on that file.

Exit codes:
    0 — always (PostToolUse hooks inform only, they cannot block).

Output on stdout is relayed to Claude so it can fix any issues.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


# Tools that modify files and should trigger lint.
_CHECKED_TOOLS = {"Write", "Edit", "write", "edit"}

# Default linter commands by file extension.
# Each value is a list of command templates.  ``{file}`` is replaced with
# the actual file path.
_DEFAULT_LINTERS: dict[str, list[list[str]]] = {
    ".py": [[sys.executable, "-m", "ruff", "check", "{file}"]],
    ".js": [["eslint", "{file}"]],
    ".ts": [["eslint", "{file}"]],
    ".tsx": [["eslint", "{file}"]],
    ".jsx": [["eslint", "{file}"]],
    ".rs": [["cargo", "clippy", "--", "-D", "warnings"]],
    ".go": [["golangci-lint", "run", "{file}"]],
}


def _read_input() -> dict[str, Any]:
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


def get_linter_commands(
    file_path: str,
    custom_linter: str | None = None,
) -> list[list[str]]:
    """Determine the linter command(s) for the given file.

    Args:
        file_path: Path to the file to lint.
        custom_linter: Optional custom linter command (shell string).
            If provided, overrides the default linter for the file type.

    Returns:
        List of command lists (argv style), with ``{file}`` substituted.
    """
    if custom_linter:
        # Split the custom command and substitute {file}
        parts = custom_linter.split()
        return [[p.replace("{file}", file_path) for p in parts]]

    ext = Path(file_path).suffix
    templates = _DEFAULT_LINTERS.get(ext, [])

    commands: list[list[str]] = []
    for template in templates:
        cmd = [part.replace("{file}", file_path) for part in template]
        commands.append(cmd)

    return commands


def run_lint(
    file_path: str,
    custom_linter: str | None = None,
    project_root: str | None = None,
) -> tuple[bool, str]:
    """Run the linter on the given file.

    Args:
        file_path: Path to the file to lint.
        custom_linter: Optional custom linter command override.
        project_root: Working directory for the linter.

    Returns:
        ``(clean, output)`` — whether the lint passed and the output.
    """
    commands = get_linter_commands(file_path, custom_linter)

    if not commands:
        return True, f"No linter configured for {Path(file_path).suffix} files."

    cwd = project_root or str(Path.cwd())
    all_output: list[str] = []
    all_clean = True

    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=cwd,
            )
            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr
            output = output.strip()

            if result.returncode != 0:
                all_clean = False
                all_output.append(output)
            else:
                all_output.append(output if output else "")

        except subprocess.TimeoutExpired:
            all_clean = False
            all_output.append(f"Lint timed out: {' '.join(cmd)}")
        except FileNotFoundError:
            # Linter not installed — skip gracefully
            all_output.append(f"Linter not found: {cmd[0]} — skipping.")

    combined = "\n".join(line for line in all_output if line)
    return all_clean, combined


def handle(
    tool_input: dict[str, Any],
    custom_linter: str | None = None,
    project_root: str | None = None,
) -> str:
    """Handle a PostToolUse event for auto-linting.

    Args:
        tool_input: Parsed tool input JSON.
        custom_linter: Optional custom linter command override.
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

    clean, output = run_lint(file_path, custom_linter, project_root)
    name = Path(file_path).name

    if clean:
        return f"[auto-lint] Lint clean: {name}"
    else:
        return f"[auto-lint] Issues found in {name}:\n{output}"


def main() -> None:
    """Entry point for the hook script."""
    tool_input = _read_input()

    if not tool_input:
        sys.exit(0)

    # Allow custom linter via environment variable
    custom_linter = os.environ.get("CHIMERA_LINTER")

    result = handle(tool_input, custom_linter)
    if result:
        print(result)

    # PostToolUse hooks always exit 0
    sys.exit(0)


if __name__ == "__main__":
    main()
