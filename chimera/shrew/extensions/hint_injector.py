"""Inject corrective hints when the agent stalls on a sub-task.

After the same sub-task fails twice in a row, small models tend to
keep trying the same approach. A short "consider X" hint snapped
into the system prompt is often enough to unstick them.

Public surface:

* :func:`should_inject_hint` — predicate over a list of recent
  attempts (each labelled with success/failure).
* :func:`build_hint` — turn a failure summary into a short hint
  string suitable for prepending to the next turn's prompt.
* :func:`inject_hint` — combine the predicate and builder into one
  call: returns ``(modified_prompt, hint_used)``.

Stdlib-only, no global state, no LLM calls — the hint text is
template-driven from the failure pattern itself.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

__all__ = [
    "Attempt",
    "MIN_FAILURES_FOR_HINT",
    "build_hint",
    "inject_hint",
    "should_inject_hint",
]


#: Minimum number of consecutive failed attempts at the same task
#: before we inject a hint. Two matches the spec — one failure can be
#: a fluke, two is a pattern.
MIN_FAILURES_FOR_HINT: Final[int] = 2


@dataclass(frozen=True)
class Attempt:
    """One attempt at a sub-task.

    Attributes:
        task: Short label for the sub-task ("read config.yaml",
            "run pytest", etc.). Equality is what defines "same task".
        succeeded: Whether the attempt succeeded.
        error: Optional short error string (used to flavour the hint).
    """

    task: str
    succeeded: bool
    error: str = ""


def should_inject_hint(
    attempts: Sequence[Attempt],
    *,
    min_failures: int = MIN_FAILURES_FOR_HINT,
) -> bool:
    """Return ``True`` when the last ``min_failures`` attempts all
    failed at the same task.

    Args:
        attempts: Recent attempts in chronological order. The tail
            is what's checked.
        min_failures: How many consecutive same-task failures trigger
            a hint. Values <= 0 always return ``False``.
    """
    if min_failures <= 0:
        return False
    if len(attempts) < min_failures:
        return False
    tail = attempts[-min_failures:]
    if any(a.succeeded for a in tail):
        return False
    first_task = tail[0].task
    return all(a.task == first_task for a in tail[1:])


def build_hint(task: str, error: str = "") -> str:
    """Produce a short corrective hint string.

    Picks a hint based on simple substring matches in ``error``;
    falls back to a generic "try a different approach" line.
    """
    err_lower = error.lower()
    if "not found" in err_lower or "no such file" in err_lower:
        suggestion = (
            "the file may not exist yet — list the directory or use "
            "a different path"
        )
    elif "permission" in err_lower:
        suggestion = "consider permission flags or a writable directory"
    elif "syntax" in err_lower or "indentation" in err_lower:
        suggestion = (
            "re-read the file before editing to match indentation and quotes"
        )
    elif "timeout" in err_lower:
        suggestion = "narrow the scope (one file, smaller test) and retry"
    elif "module" in err_lower or "import" in err_lower:
        suggestion = "check that the dependency is installed or the import path"
    else:
        suggestion = (
            "two attempts have failed — try a different approach "
            "(re-read the spec, list files, simplify the call)"
        )
    return f"[hint: {task} — {suggestion}]"


def inject_hint(
    prompt: str,
    attempts: Sequence[Attempt],
    *,
    min_failures: int = MIN_FAILURES_FOR_HINT,
) -> tuple[str, str]:
    """Conditionally prepend a hint to ``prompt``.

    Args:
        prompt: The next-turn system or user prompt.
        attempts: Recent attempt history. Only the tail is used.
        min_failures: Consecutive failures that trigger injection.

    Returns:
        ``(modified_prompt, hint_used)``. When no hint applies, the
        prompt is returned unchanged and ``hint_used`` is ``""``.
    """
    if not should_inject_hint(attempts, min_failures=min_failures):
        return prompt, ""
    last = attempts[-1]
    hint = build_hint(last.task, last.error)
    return f"{hint}\n{prompt}", hint
