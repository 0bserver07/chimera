"""Cumulative cost tracking with optional budget enforcement."""
from __future__ import annotations


class CostLimitExceeded(Exception):
    """Raised when cost budget is exceeded."""


class CostTracker:
    """Track cumulative LLM costs with optional budget.

    Args:
        budget: Maximum allowed cost in USD. None means unlimited.
    """

    def __init__(self, budget: float | None = None) -> None:
        self._total = 0.0
        self._budget = budget
        self._by_model: dict[str, float] = {}

    def record(self, cost: float, model: str = "") -> None:
        """Record a cost. Raises CostLimitExceeded if budget exceeded."""
        new_total = self._total + cost
        if self._budget is not None and new_total > self._budget:
            raise CostLimitExceeded(
                f"Cost limit exceeded: ${new_total:.4f} > ${self._budget:.4f}"
            )
        self._total = new_total
        self._by_model[model] = self._by_model.get(model, 0.0) + cost

    @property
    def total(self) -> float:
        """Total cost recorded so far."""
        return self._total

    @property
    def remaining(self) -> float | None:
        """Remaining budget, or None if no budget set."""
        if self._budget is None:
            return None
        return self._budget - self._total

    def breakdown(self) -> dict[str, float]:
        """Per-model cost breakdown."""
        return dict(self._by_model)

    def reset(self) -> None:
        """Reset all tracked costs."""
        self._total = 0.0
        self._by_model.clear()
