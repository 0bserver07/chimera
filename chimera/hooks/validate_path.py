#!/usr/bin/env python3
"""PreToolUse hook: validate file paths before Write/Edit.

A compatible coding-agent harness invokes this script as a PreToolUse
hook before Write or Edit tool calls.  It reads tool input as JSON on
stdin (falling back to the ``TOOL_INPUT`` environment variable), extracts
the ``file_path`` field, and checks whether the target file exists on
disk.

Exit codes:
    0 — allow the tool call (file exists or tool type is not checked).
    2 — block the tool call (file not found; suggestions printed to stderr).

The hook only validates tools whose ``tool_name`` is ``Write`` or ``Edit``.
All other tools pass through unconditionally.
"""
from __future__ import annotations

import json
import os
import sys
from difflib import get_close_matches
from pathlib import Path
from typing import Any


# Tools that operate on file paths and should be validated.
_CHECKED_TOOLS = {"Write", "Edit", "write", "edit"}

# Directories to skip when collecting candidate paths for suggestions.
_IGNORE_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "node_modules", ".venv", "venv", ".tox", ".eggs",
    "dist", "build", ".chimera_checkpoints",
}

# Maximum number of files to scan for fuzzy suggestions.
_MAX_SCAN_FILES = 50_000


def _read_input() -> dict[str, Any]:
    """Read tool input from stdin or TOOL_INPUT env var.

    Returns:
        Parsed JSON dict, or empty dict on failure.
    """
    # Try stdin first (non-blocking check)
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                parsed: dict[str, Any] = json.loads(raw)
                return parsed
        except (json.JSONDecodeError, OSError):
            pass

    # Fallback to environment variable
    env_val = os.environ.get("TOOL_INPUT", "")
    if env_val:
        try:
            parsed_env: dict[str, Any] = json.loads(env_val)
            return parsed_env
        except json.JSONDecodeError:
            pass

    return {}


def _collect_file_paths(root: str, max_files: int = _MAX_SCAN_FILES) -> list[str]:
    """Collect file paths under *root* for fuzzy matching.

    Args:
        root: Directory to walk.
        max_files: Stop after collecting this many paths.

    Returns:
        List of relative file paths.
    """
    paths: list[str] = []
    root_path = Path(root)
    try:
        for path in root_path.rglob("*"):
            if not path.is_file():
                continue
            # Skip ignored directories
            parts = path.relative_to(root_path).parts
            if any(p in _IGNORE_DIRS or p.startswith(".") for p in parts[:-1]):
                continue
            paths.append(str(path.relative_to(root_path)))
            if len(paths) >= max_files:
                break
    except OSError:
        pass
    return paths


def _find_suggestions(target: str, root: str, max_suggestions: int = 5) -> list[str]:
    """Find files with similar names to *target*.

    Uses both filename-based matching and full-path fuzzy matching.

    Args:
        target: The path that was not found.
        root: Workspace root to search.
        max_suggestions: Maximum suggestions to return.

    Returns:
        List of suggested file paths.
    """
    all_paths = _collect_file_paths(root)
    if not all_paths:
        return []

    target_name = Path(target).name

    # Strategy 1: exact filename match (different directory)
    exact_name = [p for p in all_paths if Path(p).name == target_name]

    # Strategy 2: fuzzy match on filename only
    all_names = [Path(p).name for p in all_paths]
    fuzzy_names = get_close_matches(target_name, all_names, n=max_suggestions * 2, cutoff=0.6)
    fuzzy_by_name = [p for p in all_paths if Path(p).name in fuzzy_names]

    # Strategy 3: fuzzy match on full path
    fuzzy_full = get_close_matches(target, all_paths, n=max_suggestions, cutoff=0.4)

    # Merge and deduplicate, preserving order
    seen: set[str] = set()
    result: list[str] = []
    for p in exact_name + fuzzy_full + fuzzy_by_name:
        if p not in seen:
            seen.add(p)
            result.append(p)
        if len(result) >= max_suggestions:
            break

    return result


def validate(tool_input: dict[str, Any]) -> tuple[bool, str]:
    """Validate a tool call's file path.

    Args:
        tool_input: Parsed tool input JSON containing ``tool_name``
            and tool-specific parameters.

    Returns:
        ``(allowed, message)`` — if *allowed* is ``False``, *message*
        contains the error/suggestion text.
    """
    tool_name = tool_input.get("tool_name", "")
    if tool_name not in _CHECKED_TOOLS:
        return True, ""

    # Extract the file path from the tool input
    # The harness puts tool params under "tool_input" or at top level
    params = tool_input.get("tool_input", tool_input)
    file_path = params.get("file_path", "") or params.get("path", "")

    if not file_path:
        return True, ""  # No path to validate — let it through

    # Resolve relative to cwd
    resolved = Path(file_path)
    if not resolved.is_absolute():
        resolved = Path.cwd() / resolved

    if resolved.exists():
        return True, ""

    # File not found — gather suggestions
    root = str(Path.cwd())
    suggestions = _find_suggestions(file_path, root)

    lines = [f"File not found: {file_path}"]
    if suggestions:
        lines.append("Did you mean one of these?")
        for s in suggestions:
            lines.append(f"  - {s}")
    else:
        lines.append("No similar files found in the workspace.")
    lines.append("")
    lines.append("If you intend to create a new file, use the Write tool instead of Edit.")

    return False, "\n".join(lines)


def main() -> None:
    """Entry point for the hook script."""
    tool_input = _read_input()

    if not tool_input:
        # No input provided — allow by default (graceful degradation)
        sys.exit(0)

    allowed, message = validate(tool_input)

    if allowed:
        sys.exit(0)
    else:
        print(message, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
