#!/usr/bin/env python3
"""PreToolUse hook: security scan for bash commands.

Claude Code calls this script as a PreToolUse hook before Bash tool calls.
It reads the tool input as JSON on stdin (falling back to the ``TOOL_INPUT``
environment variable), extracts the bash command, and checks it against
dangerous patterns using chimera's risk classifier.

Exit codes:
    0 — allow the tool call.
    2 — block the tool call (reason printed to stderr).
"""
from __future__ import annotations

import json
import os
import re
import sys


# Tools that execute commands and should be scanned.
_CHECKED_TOOLS = {"Bash", "bash", "Terminal", "terminal"}


# --------------------------------------------------------------------------
# Built-in dangerous patterns (standalone, no chimera dependency required).
# When chimera is available, we augment with its risk classifier.
# --------------------------------------------------------------------------
_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+-rf\s+/\s*$"), "recursive force delete of root filesystem"),
    (re.compile(r"\brm\s+-rf\s+/\b(?!tmp)(?!/var/tmp)"), "recursive force delete of system path"),
    (re.compile(r"\bchmod\s+777\b"), "world-writable permissions"),
    (re.compile(r"\bcurl\b.*\|\s*(?:ba)?sh\b"), "piping remote script to shell"),
    (re.compile(r"\bwget\b.*\|\s*(?:ba)?sh"), "piping remote script to shell"),
    (re.compile(r"\bcurl\b.*\|\s*python"), "piping remote script to Python"),
    (re.compile(r"\bwget\b.*\|\s*python"), "piping remote script to Python"),
    (re.compile(r"\bmkfs\b"), "formatting a filesystem"),
    (re.compile(r"\bdd\s+.*of=/dev/"), "raw disk write"),
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:"), "fork bomb"),
    (re.compile(r"\b>(?: |)/dev/sda"), "overwriting disk device"),
    (re.compile(r"\bgit\s+push\s+.*--force\s+.*main\b"), "force push to main"),
    (re.compile(r"\bgit\s+push\s+.*--force\s+.*master\b"), "force push to master"),
    (re.compile(r"\bsudo\s+rm\b"), "sudo delete"),
    (re.compile(r"\b(DROP|TRUNCATE)\s+(TABLE|DATABASE)\b", re.IGNORECASE), "destructive SQL"),
    (re.compile(r"\beval\s*\(.*\bbase64\b"), "eval of base64-encoded content"),
    (re.compile(r"\bnc\s+.*-e\s+/bin/"), "reverse shell via netcat"),
    (re.compile(r"\b/dev/tcp/"), "bash network socket access"),
]


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


def _check_with_chimera(command: str) -> tuple[bool, str]:
    """Try to use chimera's risk classifier for additional checks.

    Args:
        command: The bash command to check.

    Returns:
        ``(blocked, reason)`` — if blocked is True, reason explains why.
    """
    try:
        from chimera.permissions.risk import RiskLevel, classify_risk

        level, reason = classify_risk("bash", {"command": command})
        if level == RiskLevel.CRITICAL:
            return True, f"chimera risk classifier: {reason} (CRITICAL)"
    except ImportError:
        pass

    return False, ""


def scan_command(command: str) -> tuple[bool, str]:
    """Scan a bash command for dangerous patterns.

    Args:
        command: The bash command string.

    Returns:
        ``(allowed, reason)`` — if *allowed* is False, *reason* explains
        why the command was blocked.
    """
    if not command or not command.strip():
        return True, ""

    # Check built-in patterns
    for pattern, reason in _DANGEROUS_PATTERNS:
        if pattern.search(command):
            return False, f"Blocked: {reason}"

    # Check with chimera's classifier (if available)
    blocked, reason = _check_with_chimera(command)
    if blocked:
        return False, f"Blocked: {reason}"

    return True, ""


def handle(tool_input: dict) -> tuple[bool, str]:
    """Handle a PreToolUse event for security scanning.

    Args:
        tool_input: Parsed tool input JSON.

    Returns:
        ``(allowed, message)`` — if *allowed* is False, the command
        should be blocked and *message* explains why.
    """
    tool_name = tool_input.get("tool_name", "")
    if tool_name not in _CHECKED_TOOLS:
        return True, ""

    params = tool_input.get("tool_input", tool_input)
    command = params.get("command", "") or params.get("cmd", "")

    if not command:
        return True, ""

    return scan_command(command)


def main() -> None:
    """Entry point for the hook script."""
    tool_input = _read_input()

    if not tool_input:
        sys.exit(0)

    allowed, message = handle(tool_input)

    if allowed:
        sys.exit(0)
    else:
        print(message, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
