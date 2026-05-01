"""Cap agent output length for small models that ramble.

Sub-13B models occasionally produce long, repetitive prose between
tool calls — restating the plan, narrating what they're about to do,
or re-explaining the user's request in their own words. None of that
helps the user, and it eats KV-cache budget.

This module exposes :func:`truncate_output`, a pure helper that
clips an agent's text response to a configurable character limit
while preserving sentence boundaries when possible. A sentinel
suffix is appended when truncation occurs so downstream consumers
(e.g. the shrew CLI's renderer) can flag the cut visually.

Stdlib-only, no global state, idempotent on already-short inputs.
"""
from __future__ import annotations

from typing import Final

__all__ = [
    "DEFAULT_MAX_CHARS",
    "TRUNCATION_SUFFIX",
    "truncate_output",
]


#: Default soft limit on agent text output. 1200 characters is roughly
#: 200–300 tokens — enough for a substantive answer, short enough to
#: keep small models honest about staying terse between tool calls.
DEFAULT_MAX_CHARS: Final[int] = 1200

#: Sentinel appended to truncated outputs. Kept short so it doesn't
#: itself blow the budget when the limit is small.
TRUNCATION_SUFFIX: Final[str] = " […truncated]"


def _last_sentence_end(text: str) -> int:
    """Return the index just past the last ``. ! ?`` in ``text``.

    Returns ``-1`` when no sentence terminator is found.
    """
    best = -1
    for marker in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        idx = text.rfind(marker)
        if idx > best:
            best = idx + len(marker)
    return best


def truncate_output(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Clip ``text`` to ``max_chars`` characters, snapping to sentences.

    Args:
        text: Raw agent output.
        max_chars: Soft character cap. Values <= 0 are treated as
            "no truncation" (the input is returned unchanged).

    Returns:
        Either ``text`` unchanged (when already within budget or
        ``max_chars <= 0``) or a clipped version with
        :data:`TRUNCATION_SUFFIX` appended. When a sentence boundary
        exists in the second half of the budget window, we cut there;
        otherwise we hard-cut at ``max_chars``.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text

    head = text[:max_chars]
    boundary = _last_sentence_end(head)
    if boundary >= max_chars // 2:
        head = head[:boundary].rstrip()
    return head.rstrip() + TRUNCATION_SUFFIX
