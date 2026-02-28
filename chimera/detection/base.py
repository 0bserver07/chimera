"""Base abstractions for loop / repetition detection."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

__all__ = ["DetectionResult", "DetectionStrategy"]


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Outcome returned when a detection strategy fires.

    Attributes:
        detected: Whether a loop / repetition was found.
        strategy: Name of the strategy that triggered (e.g. ``"exact_repeat"``).
        pattern: Human-readable description of what was detected.
        confidence: A value in ``[0, 1]`` expressing certainty. Defaults to 1.0.
    """

    detected: bool
    strategy: str
    pattern: str
    confidence: float = 1.0


class DetectionStrategy(ABC):
    """Interface that every concrete detector must implement."""

    @abstractmethod
    def record(self, tool_name: str, args: dict[str, Any]) -> None:
        """Record a tool invocation for later analysis."""

    @abstractmethod
    def check(self) -> DetectionResult | None:
        """Return a *DetectionResult* if a loop is detected, else ``None``."""

    @abstractmethod
    def reset(self) -> None:
        """Clear all internal state."""
