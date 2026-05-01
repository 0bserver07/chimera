"""Detect short loops in small-model output streams.

Small models routinely fall into 2- or 3-step repetition loops —
emitting the same tool call, same observation summary, or same
"let me try X" preamble turn after turn. This module ships pure
detectors the shrew loop can call to short-circuit before the loop
budget runs out.

Public surface:

* :func:`detect_short_loop` — returns ``True`` when the tail of a
  sequence matches a recent prefix of itself.
* :func:`should_short_circuit` — convenience wrapper that combines
  loop detection with a minimum-length guard.

Stdlib-only, pure functions, no state retained between calls.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Final

__all__ = [
    "DEFAULT_MIN_REPEATS",
    "DEFAULT_WINDOW",
    "detect_short_loop",
    "should_short_circuit",
]


#: Minimum number of consecutive repetitions before we call it a
#: loop. Two is the smallest meaningful value — one repetition is
#: just a re-emit, three would catch fewer pathologies.
DEFAULT_MIN_REPEATS: Final[int] = 2

#: Maximum cycle length to look for. Small models almost never form
#: loops longer than four steps; capping the search keeps the helper
#: O(n*window) rather than O(n^2).
DEFAULT_WINDOW: Final[int] = 4


def detect_short_loop(
    items: Sequence[object],
    *,
    window: int = DEFAULT_WINDOW,
    min_repeats: int = DEFAULT_MIN_REPEATS,
) -> int:
    """Return the cycle length of a detected tail loop, or 0.

    Scans cycle lengths ``1..window`` and returns the smallest
    ``k`` for which the last ``k * (min_repeats + 1)`` items of
    ``items`` consist of ``min_repeats + 1`` identical ``k``-step
    chunks. Returns ``0`` when no loop is found.

    Args:
        items: Sequence of comparable, hashable-or-not items.
            Equality is checked with ``==``.
        window: Largest cycle length considered.
        min_repeats: How many full additional cycles must follow the
            seed cycle for us to call it a loop. ``2`` means three
            consecutive identical chunks.

    Returns:
        The detected cycle length (``>=1``) or ``0`` when no loop is
        present.
    """
    if window <= 0 or min_repeats <= 0:
        return 0
    n = len(items)
    if n == 0:
        return 0

    max_k = min(window, n // (min_repeats + 1))
    for k in range(1, max_k + 1):
        needed = k * (min_repeats + 1)
        if n < needed:
            continue
        tail = items[n - needed:]
        seed = tail[:k]
        if all(tail[i * k:(i + 1) * k] == seed for i in range(min_repeats + 1)):
            return k
    return 0


def should_short_circuit(
    items: Sequence[object],
    *,
    window: int = DEFAULT_WINDOW,
    min_repeats: int = DEFAULT_MIN_REPEATS,
    min_length: int = 4,
) -> bool:
    """Return ``True`` when the loop is real enough to break out.

    Adds a ``min_length`` guard so very short sequences (e.g. the
    first three turns of a conversation) don't trigger spurious
    short-circuits.
    """
    if len(items) < min_length:
        return False
    return detect_short_loop(items, window=window, min_repeats=min_repeats) > 0
