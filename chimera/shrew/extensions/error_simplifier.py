"""Rewrite tool errors into one-liners small models can act on.

Frontier models cope with raw stack traces. Small models do not —
they latch onto the deepest frame, miss the actual root cause, and
loop. This module rewrites common Python / shell / filesystem error
strings into short, actionable English suitable for direct
injection into the agent's observation channel.

Public surface:

* :func:`simplify_error` — one-shot rewrite, never raises.
* :func:`is_known_error` — predicate exposed so callers can decide
  whether to use the simplified form or fall back to the original.

Stdlib-only, regex + table-driven, pure functions.
"""
from __future__ import annotations

import re
from typing import Final

__all__ = [
    "MAX_SIMPLE_CHARS",
    "is_known_error",
    "simplify_error",
]


#: Hard cap on the simplified error output. We never exceed this so
#: the agent's observation channel stays small-model-friendly.
MAX_SIMPLE_CHARS: Final[int] = 240


# (regex, replacement template using \1 \2 etc.) — order matters;
# more specific patterns first.
_RULES: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (
        re.compile(
            r"FileNotFoundError:.*?'([^']+)'",
            re.IGNORECASE | re.DOTALL,
        ),
        "File not found: {0}. Check the path or list the directory first.",
    ),
    (
        re.compile(
            r"\[Errno 2\] No such file or directory: ['\"]([^'\"]+)['\"]",
        ),
        "File not found: {0}. Check the path or list the directory first.",
    ),
    (
        re.compile(
            r"PermissionError:.*?'([^']+)'",
            re.IGNORECASE | re.DOTALL,
        ),
        "Permission denied for {0}. Try a different path or chmod first.",
    ),
    (
        re.compile(
            r"ModuleNotFoundError: No module named ['\"]([^'\"]+)['\"]",
        ),
        "Missing module: {0}. Install it or check the import name.",
    ),
    (
        re.compile(
            r"ImportError: cannot import name ['\"]([^'\"]+)['\"]",
        ),
        "Import failed: '{0}' is not exported. Check the symbol name.",
    ),
    (
        re.compile(
            r"SyntaxError:\s*(.+?)(?:\n|$)",
        ),
        "Syntax error: {0}. Re-read the file before editing.",
    ),
    (
        re.compile(
            r"IndentationError:\s*(.+?)(?:\n|$)",
        ),
        "Indentation error: {0}. Match existing tabs/spaces.",
    ),
    (
        re.compile(
            r"NameError: name ['\"]([^'\"]+)['\"] is not defined",
        ),
        "Undefined name '{0}'. Import it or check spelling.",
    ),
    (
        re.compile(
            r"AttributeError:.*?has no attribute ['\"]([^'\"]+)['\"]",
            re.DOTALL,
        ),
        "No such attribute '{0}'. Inspect the object first.",
    ),
    (
        re.compile(
            r"TypeError:\s*(.+?)(?:\n|$)",
        ),
        "Type error: {0}. Check the function signature.",
    ),
    (
        re.compile(
            r"command not found:?\s*(\S+)",
            re.IGNORECASE,
        ),
        "Command not found: {0}. Check spelling or install it.",
    ),
    (
        re.compile(
            r"timeout(?:\s+expired)?",
            re.IGNORECASE,
        ),
        "Operation timed out. Try a smaller scope or longer timeout.",
    ),
)


def _apply_rule(error: str) -> str | None:
    for pattern, template in _RULES:
        match = pattern.search(error)
        if match:
            groups = tuple(g if g is not None else "" for g in match.groups())
            try:
                return template.format(*groups)
            except (IndexError, KeyError):
                return template
    return None


def is_known_error(error: str) -> bool:
    """Return ``True`` when ``error`` matches a known simplification rule."""
    if not error:
        return False
    return _apply_rule(error) is not None


def simplify_error(error: str) -> str:
    """Rewrite ``error`` as a small-model-friendly one-liner.

    Args:
        error: Raw error string (single line or full traceback).

    Returns:
        Either a rewritten one-liner (when the error matches a known
        rule) or a length-capped first line of the input. Never
        raises; ``""`` returns ``""``.
    """
    if not error:
        return ""

    rewritten = _apply_rule(error)
    if rewritten is not None:
        if len(rewritten) > MAX_SIMPLE_CHARS:
            return rewritten[: MAX_SIMPLE_CHARS - 3] + "..."
        return rewritten

    # Fallback: take the last non-empty line (the actual exception)
    # and cap. The last line of a Python traceback is the message.
    lines = [ln.strip() for ln in error.splitlines() if ln.strip()]
    last = lines[-1] if lines else error.strip()
    if len(last) > MAX_SIMPLE_CHARS:
        return last[: MAX_SIMPLE_CHARS - 3] + "..."
    return last
