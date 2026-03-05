"""Risk classification for tool calls."""
from __future__ import annotations

import re
from enum import Enum


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Dangerous bash patterns
_DANGEROUS_PATTERNS = [
    (re.compile(r"\brm\s+-rf\b"), RiskLevel.CRITICAL, "recursive force delete"),
    (re.compile(r"\brm\s+-r\b"), RiskLevel.HIGH, "recursive delete"),
    (re.compile(r"\bchmod\s+777\b"), RiskLevel.HIGH, "world-writable permissions"),
    (re.compile(r"\bcurl\b.*\|\s*sh"), RiskLevel.CRITICAL, "pipe remote script to shell"),
    (re.compile(r"\bwget\b.*\|\s*sh"), RiskLevel.CRITICAL, "pipe remote script to shell"),
    (re.compile(r"\bgit\s+push\s+.*--force\b"), RiskLevel.HIGH, "force push"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), RiskLevel.HIGH, "hard reset"),
    (re.compile(r"\bsudo\b"), RiskLevel.HIGH, "elevated privileges"),
    (re.compile(r"\b(DROP|DELETE|TRUNCATE)\s+"), RiskLevel.CRITICAL, "destructive SQL"),
]

# Sensitive file patterns
_SENSITIVE_PATHS = [
    (re.compile(r"\.env$"), RiskLevel.HIGH, "environment file"),
    (re.compile(r"\.ssh/"), RiskLevel.CRITICAL, "SSH directory"),
    (re.compile(r"(credentials|secrets|tokens)\.(json|yaml|yml|toml)$", re.I), RiskLevel.CRITICAL, "credentials file"),
    (re.compile(r"/etc/"), RiskLevel.HIGH, "system config"),
    (re.compile(r"\.git/config$"), RiskLevel.MEDIUM, "git config"),
]

# Tool risk defaults
_TOOL_DEFAULTS: dict[str, RiskLevel] = {
    "read_file": RiskLevel.LOW,
    "search": RiskLevel.LOW,
    "list_files": RiskLevel.LOW,
    "repo_map": RiskLevel.LOW,
    "write_file": RiskLevel.MEDIUM,
    "edit_file": RiskLevel.MEDIUM,
    "replace_in_file": RiskLevel.MEDIUM,
    "bash": RiskLevel.MEDIUM,
    "git": RiskLevel.MEDIUM,
    "test": RiskLevel.LOW,
    "web_fetch": RiskLevel.MEDIUM,
    "delegate": RiskLevel.LOW,
}


def classify_risk(tool_name: str, arguments: dict) -> tuple[RiskLevel, str]:
    """Classify the risk level of a tool call.

    Returns (risk_level, reason).
    """
    # Check bash commands for dangerous patterns
    if tool_name == "bash":
        cmd = arguments.get("command", "") or arguments.get("cmd", "")
        for pattern, level, reason in _DANGEROUS_PATTERNS:
            if pattern.search(cmd):
                return level, reason

    # Check file paths for sensitive patterns
    path = arguments.get("path", "") or arguments.get("file", "")
    if path:
        for pattern, level, reason in _SENSITIVE_PATHS:
            if pattern.search(path):
                return level, reason

    # Fall back to tool defaults
    default = _TOOL_DEFAULTS.get(tool_name, RiskLevel.MEDIUM)
    return default, ""


def format_risk(level: RiskLevel) -> str:
    """Format risk level for display."""
    icons = {
        RiskLevel.LOW: "[LOW]",
        RiskLevel.MEDIUM: "[MEDIUM]",
        RiskLevel.HIGH: "[HIGH]",
        RiskLevel.CRITICAL: "[CRITICAL]",
    }
    return icons.get(level, "[UNKNOWN]")
