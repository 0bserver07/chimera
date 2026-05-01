"""``chimera badger`` rerun-on-failure logic.

When the agent's first attempt produces a failure marker — a test failure
in tool output, a Python or JS syntax-error trace, an obvious stack
trace, or an explicit ``FAILED`` line — the rerun helper resets the
conversation and retries with a refined prompt up to ``max_reruns``
extra attempts.

The detection is **deliberately conservative**: we only rerun when the
failure markers are unambiguous. False negatives (a real failure that
slips by) are preferable to false positives (rerunning a healthy
trajectory and burning more tokens than necessary). The harness-rewrite
posture treats rerun cost as a real budget, not a free safety net.

Public surface:

* :func:`detect_failure_markers` — pure function: scan a result string
  for tell-tale failure markers. Returns a list of human-readable
  reasons; empty list means "no failure detected".
* :func:`refine_prompt_for_rerun` — pure function: given the original
  prompt and detected failure reasons, return a refined prompt that
  asks the agent to address the specific failure markers.
* :func:`run_with_rerun` — async coroutine: drive an agent through up
  to ``1 + max_reruns`` attempts, returning the last (or successful)
  result.

Trademark hygiene: the rerun pattern is a generic harness technique,
not a brand. We name it ``rerun-on-failure`` to keep the upstream out
of the source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "FAILURE_PATTERNS",
    "RerunOutcome",
    "detect_failure_markers",
    "refine_prompt_for_rerun",
    "run_with_rerun",
]


# WHY: the patterns are ordered by specificity. The most specific
# (pytest's "FAILED" line, an explicit "SyntaxError:") come first so the
# reason list reads from "most actionable" to "least actionable".
FAILURE_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    (
        "pytest test failure",
        re.compile(r"^FAILED\s+\S+::", re.MULTILINE),
    ),
    (
        "pytest summary",
        re.compile(r"^=+\s*\d+\s+failed[ ,].*=+$", re.MULTILINE),
    ),
    (
        "Python syntax error",
        re.compile(r"^\s*SyntaxError\b", re.MULTILINE),
    ),
    (
        "Python traceback",
        re.compile(
            r"^Traceback \(most recent call last\):", re.MULTILINE,
        ),
    ),
    (
        "JavaScript syntax error",
        re.compile(r"\bSyntaxError\b.*Unexpected", re.MULTILINE),
    ),
    (
        "Rust compile error",
        re.compile(r"^error\[E\d+\]", re.MULTILINE),
    ),
    (
        "node assert error",
        re.compile(r"\bAssertionError(?:\s*\[ERR_\w+\])?:", re.MULTILINE),
    ),
    (
        "explicit failure marker",
        re.compile(
            r"^(BUILD FAILED|TESTS FAILED|FAILURE|ERROR:.*failed)\b",
            re.MULTILINE,
        ),
    ),
]


@dataclass
class RerunOutcome:
    """Result of a rerun-on-failure session.

    Attributes:
        result: The final agent result (whatever ``agent.async_run``
            returned on the last attempt).
        attempts: Total attempts made (1 + reruns).
        triggered_reasons: Per-attempt list of detected failure reasons.
            Index ``i`` holds reasons that triggered rerun number
            ``i+1``; empty entries mean "no rerun triggered" (typically
            the last entry on success).
    """

    result: Any
    attempts: int
    triggered_reasons: list[list[str]]


def detect_failure_markers(text: str) -> list[str]:
    """Scan *text* for unambiguous failure markers.

    Args:
        text: The text to scan (typically ``result.output`` plus any
            captured tool output).

    Returns:
        A list of human-readable reasons (e.g.
        ``["pytest test failure", "Python traceback"]``). Empty when
        no markers fire.
    """
    if not text:
        return []
    reasons: list[str] = []
    for label, pattern in FAILURE_PATTERNS:
        if pattern.search(text):
            reasons.append(label)
    return reasons


def _extract_text(result: Any) -> str:
    """Pull a search-friendly string out of an agent result.

    Different result objects expose the trajectory differently. We
    concatenate the canonical fields and let :func:`detect_failure_markers`
    decide. Missing fields are silently skipped.
    """
    parts: list[str] = []
    output = getattr(result, "output", None)
    if isinstance(output, str):
        parts.append(output)
    error = getattr(result, "error", None)
    if isinstance(error, str):
        parts.append(error)
    # Tool history (when the agent exposes it). We accept ``messages``,
    # ``tool_results``, or ``trajectory`` lists of arbitrary shape.
    for attr in ("tool_results", "messages", "trajectory"):
        items = getattr(result, attr, None)
        if not items:
            continue
        for item in items:
            if isinstance(item, str):
                parts.append(item)
                continue
            for key in ("content", "output", "result", "text"):
                v = getattr(item, key, None) if not isinstance(item, dict) else item.get(key)
                if isinstance(v, str):
                    parts.append(v)
    return "\n".join(parts)


def refine_prompt_for_rerun(
    original: str,
    reasons: list[str],
    *,
    attempt: int,
) -> str:
    """Build a refined prompt for the next rerun attempt.

    The refined prompt prepends a short, focused directive that names
    the detected failure markers and asks the agent to address them
    before declaring success. Mirrors the harness-rewrite tradition's
    "verify before claiming done" discipline.

    Args:
        original: The original user prompt.
        reasons: The list of failure reasons that triggered the rerun.
        attempt: Which rerun attempt this is (1-indexed; 1 = first
            rerun after the initial attempt).

    Returns:
        A new prompt string with the directive prepended.
    """
    reason_blob = ", ".join(reasons) if reasons else "an unspecified failure"
    directive = (
        f"[badger rerun {attempt}] The previous attempt produced "
        f"{reason_blob}. Re-read the failing output, isolate the root "
        "cause, fix it, and re-run any failing tests before reporting "
        "completion. Do not claim done until verification passes.\n\n"
        "Original task:\n"
    )
    return directive + original


async def run_with_rerun(
    agent: Any,
    prompt: str,
    *,
    env: Any = None,
    max_reruns: int = 2,
) -> Any:
    """Drive *agent* through up to ``1 + max_reruns`` attempts.

    Args:
        agent: An object exposing ``async_run(prompt, env=...) -> result``.
        prompt: The user prompt for the first attempt.
        env: Optional environment passed through to ``async_run``.
        max_reruns: Maximum extra attempts after the first. Total
            attempts = ``1 + max_reruns``. ``max_reruns <= 0`` disables
            rerun entirely (single attempt).

    Returns:
        The result from the successful attempt, or the final attempt's
        result when every attempt produced a failure marker.
    """
    attempts_total = 1 + max(0, int(max_reruns))
    last_result: Any = None
    current_prompt = prompt

    for attempt_idx in range(attempts_total):
        last_result = await agent.async_run(current_prompt, env=env)

        # If the agent already reports success and we don't see failure
        # markers, accept the result.
        success_flag = bool(getattr(last_result, "success", False))
        text = _extract_text(last_result)
        reasons = detect_failure_markers(text)

        if success_flag and not reasons:
            return last_result
        if not reasons:
            # Agent reported failure (or no success flag) but no
            # actionable markers fired. Return the result as-is so the
            # caller can decide what to do; rerun would be guesswork.
            return last_result

        if attempt_idx + 1 >= attempts_total:
            # Last attempt; return the failed result.
            return last_result

        # Refine the prompt for the next attempt.
        current_prompt = refine_prompt_for_rerun(
            prompt, reasons, attempt=attempt_idx + 1,
        )

    return last_result
