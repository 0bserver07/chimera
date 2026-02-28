"""Composite detector that delegates to multiple strategies."""
from __future__ import annotations

from typing import Any

from chimera.detection.base import DetectionResult, DetectionStrategy

__all__ = ["CompositeDetector"]


class CompositeDetector(DetectionStrategy):
    """Fan-out to a list of strategies; return the first positive result.

    Parameters:
        strategies: Ordered collection of :class:`DetectionStrategy` instances.
            ``record`` is forwarded to **all** of them; ``check`` returns the
            first non-``None`` :class:`DetectionResult`.
    """

    def __init__(self, strategies: list[DetectionStrategy]) -> None:
        if not strategies:
            raise ValueError("CompositeDetector requires at least one strategy")
        self._strategies = list(strategies)

    # -- DetectionStrategy interface -----------------------------------------

    def record(self, tool_name: str, args: dict[str, Any]) -> None:
        for strategy in self._strategies:
            strategy.record(tool_name, args)

    def check(self) -> DetectionResult | None:
        for strategy in self._strategies:
            result = strategy.check()
            if result is not None:
                return result
        return None

    def reset(self) -> None:
        for strategy in self._strategies:
            strategy.reset()
