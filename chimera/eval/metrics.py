from __future__ import annotations

import math
from typing import Any


def pass_at_k(n: int, c: int, k: int) -> float:
    """Compute pass@k metric.

    Args:
        n: Total number of samples generated per problem.
        c: Number of correct samples.
        k: k value for pass@k.

    Returns:
        Estimated probability that at least one of k samples is correct.
    """
    if n < k:
        raise ValueError(f"n ({n}) must be >= k ({k})")
    if n - c < k:
        return 1.0
    return 1.0 - math.prod((n - c - i) / (n - i) for i in range(k))


def avg_cost(results: list[Any]) -> float:
    """Average cost across eval results."""
    if not results:
        return 0.0
    return sum(r.cost for r in results) / len(results)


def avg_steps(results: list[Any]) -> float:
    """Average steps across eval results."""
    if not results:
        return 0.0
    return sum(r.steps for r in results) / len(results)


def resolve_rate(results: list[Any]) -> float:
    """Fraction of tasks resolved (passed)."""
    if not results:
        return 0.0
    return sum(1 for r in results if r.passed) / len(results)
