"""Typed failure taxonomy for benchmark-matrix cells.

:func:`~chimera.eval.matrix.run_matrix` records each cell with a bare terminal
status string (``completed`` / ``budget_exhausted`` / ``error`` / ``timeout``)
plus a free-text note. That says *whether* a cell failed but not *why* — an
``error`` cell could be a rate-limited provider, malformed model output, a
crashed tool, or a grader that raised. This module maps a cell's
``(status, error_msg)`` onto a small, stable :class:`FailureCategory` so reports
can aggregate failures by root cause instead of re-parsing prose at every call
site.

The classifier is deterministic and substring based — no network, no model, no
randomness — so identical inputs always yield the same category, keeping it a
controlled variable like the rest of the matrix harness.
"""

from __future__ import annotations

import enum


class FailureCategory(str, enum.Enum):
    """Root-cause bucket for a benchmark-matrix cell outcome.

    Members are string valued (``FailureCategory.TIMEOUT == "timeout"``) so a
    category serializes directly into the JSON matrix reports.

    Attributes:
        BUDGET_EXHAUSTED: The agent hit its tool-call / cost / wall-clock budget
            before finishing.
        TOOL_ERROR: A tool — or the subprocess / OS call behind it — failed
            during execution.
        PARSE_ERROR: Output could not be parsed or decoded (e.g. malformed
            JSON).
        EMPTY_OUTPUT: The agent produced nothing gradeable (no answer / no final
            message).
        PROVIDER_ERROR: The model provider failed — rate limit, HTTP error,
            dropped connection, or API-client error.
        TIMEOUT: The attempt exceeded its time limit.
        GRADER_ERROR: Grading / evaluation itself raised.
        UNKNOWN: No rule matched, or the status was a success (which carries no
            failure to categorize).
    """

    BUDGET_EXHAUSTED = "budget_exhausted"
    TOOL_ERROR = "tool_error"
    PARSE_ERROR = "parse_error"
    EMPTY_OUTPUT = "empty_output"
    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"
    GRADER_ERROR = "grader_error"
    UNKNOWN = "unknown"


#: Terminal statuses that denote success, so there is no failure to categorize.
#: The matrix uses ``completed``; ``ok`` / ``success`` / ``passed`` are accepted
#: defensively so callers with a different vocabulary still short-circuit.
_SUCCESS_STATUSES: frozenset[str] = frozenset({"ok", "completed", "success", "passed"})

#: Statuses that map straight to a category, before any message inspection.
_STATUS_CATEGORIES: dict[str, FailureCategory] = {
    "budget_exhausted": FailureCategory.BUDGET_EXHAUSTED,
    "timeout": FailureCategory.TIMEOUT,
}

#: Ordered ``(category, signatures)`` rules for inspecting a free-text message.
#: Order is priority: the first category with any matching substring wins, so
#: more specific / infrastructural causes precede generic ones. Signatures are
#: matched case-insensitively against the lowercased message. Provider failures
#: are checked before timeouts so a dropped-connection timeout ("connection
#: timed out") reads as a provider fault rather than the agent's own clock.
_MESSAGE_RULES: tuple[tuple[FailureCategory, tuple[str, ...]], ...] = (
    (
        FailureCategory.PROVIDER_ERROR,
        (
            "rate limit",
            "rate_limit",
            "ratelimit",
            "too many requests",
            "429",
            "overloaded",
            "quota",
            "connection",
            "connect",
            "network",
            "unauthorized",
            "forbidden",
            "bad gateway",
            "service unavailable",
            "internal server error",
            "apierror",
            "api error",
            "api_error",
            "api key",
            "api request",
            "api call",
            "apiconnection",
            "provider",
            "socket",
            "ssl",
        ),
    ),
    (
        FailureCategory.TIMEOUT,
        ("timeout", "timed out", "timedout", "deadline exceeded"),
    ),
    (
        FailureCategory.TOOL_ERROR,
        (
            "tool error",
            "toolerror",
            "tool_error",
            "tool execution",
            "tool call",
            "tool failed",
            "subprocess",
            "command failed",
            "nonzero exit",
            "non-zero exit",
            "exit code",
            "returncode",
            "permission denied",
            "no such file",
            "filenotfound",
            "isadirectory",
            "oserror",
        ),
    ),
    (
        FailureCategory.GRADER_ERROR,
        ("grader", "grading", "evaluate", "evaluation"),
    ),
    (
        FailureCategory.EMPTY_OUTPUT,
        (
            "empty",
            "no output",
            "no final answer",
            "no answer",
            "no final message",
            "blank",
            "nothing to grade",
        ),
    ),
    (
        FailureCategory.PARSE_ERROR,
        (
            "json",
            "parse",
            "decode",
            "malformed",
            "unmarshal",
            "deserialize",
            "invalid syntax",
        ),
    ),
)


def classify_failure(status: str, error_msg: str | None = None) -> FailureCategory:
    """Map a matrix cell's ``(status, error_msg)`` onto a :class:`FailureCategory`.

    Rules are applied in this fixed order:

    1. **Success short-circuit.** A success status (``ok``, ``completed``,
       ``success``, ``passed``) has no failure to categorize and returns
       :attr:`FailureCategory.UNKNOWN` *without* inspecting the message — a
       completed cell's note is a budget remark, not an error.
    2. **Status mapping.** ``budget_exhausted`` →
       :attr:`FailureCategory.BUDGET_EXHAUSTED` and ``timeout`` →
       :attr:`FailureCategory.TIMEOUT`, regardless of the message.
    3. **Message signatures.** For any other status (typically ``error``), the
       lowercased *error_msg* is scanned for substring signatures; the first
       category to match wins, in this priority order:

       - :attr:`FailureCategory.PROVIDER_ERROR` — provider / model-API faults:
         ``rate limit`` / ``429`` / ``too many requests`` / HTTP 4xx-5xx phrases
         / ``connection`` / ``overloaded`` / ``quota`` / API-client error names.
       - :attr:`FailureCategory.TIMEOUT` — ``timeout`` / ``timed out`` /
         ``deadline exceeded`` (checked after provider so a dropped-connection
         timeout classifies as a provider fault).
       - :attr:`FailureCategory.TOOL_ERROR` — tool, subprocess, or OS-level file
         errors: ``tool ...`` / ``subprocess`` / ``exit code`` /
         ``permission denied`` / ``no such file``.
       - :attr:`FailureCategory.GRADER_ERROR` — grading itself raised:
         ``grader`` / ``grading`` / ``evaluate`` / ``evaluation``.
       - :attr:`FailureCategory.EMPTY_OUTPUT` — nothing gradeable: ``empty`` /
         ``no output`` / ``no final answer``.
       - :attr:`FailureCategory.PARSE_ERROR` — malformed output: ``json`` /
         ``parse`` / ``decode`` / ``malformed``.
    4. **Fallback.** Anything unmatched — including an ``error`` status with an
       empty or ``None`` message, or an unrecognized status — returns
       :attr:`FailureCategory.UNKNOWN`.

    Args:
        status: The cell's terminal status string. Matched case-insensitively
            with surrounding whitespace ignored. Known values are ``completed``
            / ``ok`` / ``budget_exhausted`` / ``error`` / ``timeout``.
        error_msg: Optional free-text error or note for the cell. ``None`` or an
            empty string is treated as "no signal".

    Returns:
        The :class:`FailureCategory` that best explains the cell's outcome.
    """
    normalized_status = (status or "").strip().lower()

    if normalized_status in _SUCCESS_STATUSES:
        return FailureCategory.UNKNOWN

    status_category = _STATUS_CATEGORIES.get(normalized_status)
    if status_category is not None:
        return status_category

    message = (error_msg or "").lower()
    if message:
        for category, signatures in _MESSAGE_RULES:
            if any(signature in message for signature in signatures):
                return category

    return FailureCategory.UNKNOWN
