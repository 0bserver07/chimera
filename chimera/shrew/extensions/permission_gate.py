"""Shrew bash permission gate (W15-2 P2 / LITTLE-CODER GAP-EXT-7).

Small models routinely propose dangerous bash commands when the prompt
nudges them in that direction (``rm -rf .``, ``curl | sh``, ``sudo
chown -R``). The upstream little-coder ships a 3-mode env-var gate
(``LITTLE_CODER_PERMISSION_MODE=auto/manual/accept-all``) that classifies
each invocation against a built-in risk table and either auto-approves,
prompts the operator, or auto-denies.

This module is the shrew port. It is stdlib-only and does *not* execute
any commands — callers feed in a candidate command string and read back
a decision tag (``allow`` / ``ask`` / ``deny``). The integration site is
shrew's tool wrapper; tests below exercise the classifier in isolation
so the gate can be reasoned about without booting the agent.
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "Decision",
    "GateMode",
    "RiskLevel",
    "DANGEROUS_PATTERNS",
    "READ_ONLY_PREFIXES",
    "classify_command",
    "evaluate_command",
    "resolve_mode",
]

GateMode = Literal["auto", "manual", "accept-all"]
"""Three operating modes mirroring upstream's ``LITTLE_CODER_PERMISSION_MODE``."""

RiskLevel = Literal["safe", "moderate", "dangerous"]
"""Risk classification for a candidate command."""

Decision = Literal["allow", "ask", "deny"]
"""Final gate output: allow without prompting, prompt the operator, or deny outright."""


# Read-only / safe shell builtins. The shrew agent gets these for free
# (no prompt) in every mode. Keep this list narrow — anything ambiguous
# (e.g. ``find`` with ``-delete``) should fall through to the dangerous
# matcher instead of being whitelisted by prefix.
READ_ONLY_PREFIXES: tuple[str, ...] = (
    "ls",
    "pwd",
    "echo",
    "cat",
    "head",
    "tail",
    "wc",
    "stat",
    "file",
    "which",
    "type",
    "env",
    "printenv",
    "grep",
    "rg",
    "ag",
    "ack",
    "find",  # safe by default; -delete escalates via DANGEROUS_PATTERNS
    "git status",
    "git log",
    "git diff",
    "git show",
    "git branch",
    "python --version",
    "node --version",
    "pip show",
    "pytest --collect-only",
)


# Dangerous patterns. Each is a regex; a match anywhere in the command
# string flags the command as ``dangerous``. The list intentionally
# casts a wide net — false positives prompt the operator; false
# negatives skip the prompt and may damage the workspace.
DANGEROUS_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"\brm\s+-rf?\b",
        r"\brm\s+-r\s+\.",
        r"\bsudo\b",
        r"\bdd\s+if=",
        r"\bmkfs\b",
        r"\b:>\s*/\w",  # >/dev/sda style truncate
        r"\bcurl[^|;]+\|\s*(?:bash|sh|zsh|python|node)\b",
        r"\bwget[^|;]+\|\s*(?:bash|sh|zsh|python|node)\b",
        r"\bgit\s+push\s+--force\b",
        r"\bgit\s+push\s+-f\b",
        r"\bgit\s+reset\s+--hard\b",
        r"\bchmod\s+-R\s+777\b",
        r"\bchown\s+-R\b",
        r"\bfind\s+[^\n;]+-delete\b",
        r">\s*/dev/sd[a-z]\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bhalt\b",
        r"\bkill\s+-9\s+1\b",
        r"\beval\s+\$\(.*\)",
    )
)


@dataclass(frozen=True)
class _Classification:
    """Internal classifier output."""

    level: RiskLevel
    matched: str | None


def _classify_internal(command: str) -> _Classification:
    """Classify *command* without applying a mode."""
    text = (command or "").strip()
    if not text:
        return _Classification("safe", None)

    for pat in DANGEROUS_PATTERNS:
        m = pat.search(text)
        if m is not None:
            return _Classification("dangerous", m.group(0))

    # Read-only prefix check. We compare on tokenised form so
    # ``ls -la /tmp`` matches ``ls`` but ``lsblk`` does NOT match
    # ``ls`` (different first token).
    try:
        head = shlex.split(text, posix=True)
    except ValueError:
        head = text.split()
    if not head:
        return _Classification("safe", None)
    first = head[0]
    two = " ".join(head[:2]) if len(head) >= 2 else first
    for prefix in READ_ONLY_PREFIXES:
        if first == prefix or two == prefix:
            return _Classification("safe", prefix)

    return _Classification("moderate", None)


def classify_command(command: str) -> RiskLevel:
    """Return ``"safe"``/``"moderate"``/``"dangerous"`` for *command*.

    Args:
        command: Raw shell command the model wants to run. Empty or
            whitespace-only strings classify as ``"safe"``.
    """
    return _classify_internal(command).level


def resolve_mode(env: dict[str, str] | None = None) -> GateMode:
    """Resolve the active gate mode from env vars.

    Args:
        env: Optional explicit env mapping (defaults to :data:`os.environ`).
            Lookup precedence: ``SHREW_PERMISSION_MODE`` >
            ``LITTLE_CODER_PERMISSION_MODE`` (upstream alias) > ``"auto"``.

    Returns:
        One of the three :data:`GateMode` literals. An unknown value is
        coerced to ``"auto"`` for safety.
    """
    src = env if env is not None else os.environ
    raw = (
        src.get("SHREW_PERMISSION_MODE")
        or src.get("LITTLE_CODER_PERMISSION_MODE")
        or "auto"
    ).strip().lower()
    if raw in ("auto", "manual", "accept-all"):
        return raw  # type: ignore[return-value]
    return "auto"


def evaluate_command(
    command: str,
    *,
    mode: GateMode | None = None,
    env: dict[str, str] | None = None,
) -> Decision:
    """Combine classification + mode to produce a final decision.

    Mode semantics:

    * ``accept-all`` — every command returns ``"allow"`` (the upstream
      "I trust the model" mode; useful for CI sandboxes).
    * ``manual`` — every command returns ``"ask"`` (operator stays in
      the loop on every invocation).
    * ``auto`` — safe commands ``allow``, moderate commands ``ask``,
      dangerous commands ``deny``.

    Args:
        command: Candidate shell command.
        mode: Override mode. ``None`` reads :data:`resolve_mode` from env.
        env: Optional env mapping; passed to :func:`resolve_mode`.
    """
    chosen = mode or resolve_mode(env)
    if chosen == "accept-all":
        return "allow"
    level = classify_command(command)
    if chosen == "manual":
        if level == "dangerous":
            return "deny"
        return "ask"
    # auto
    if level == "safe":
        return "allow"
    if level == "moderate":
        return "ask"
    return "deny"
