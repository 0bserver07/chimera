"""Thinking level abstraction for extended reasoning."""
from __future__ import annotations

from enum import Enum


class ThinkingLevel(str, Enum):
    """Controls the depth of LLM reasoning/thinking."""
    OFF = "off"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


THINKING_BUDGETS: dict[ThinkingLevel, int] = {
    ThinkingLevel.OFF: 0,
    ThinkingLevel.MINIMAL: 1024,
    ThinkingLevel.LOW: 2048,
    ThinkingLevel.MEDIUM: 8192,
    ThinkingLevel.HIGH: 16384,
    ThinkingLevel.MAX: 32768,
}


def budget_for_level(level: ThinkingLevel) -> int:
    """Return the token budget for a thinking level."""
    return THINKING_BUDGETS.get(level, 0)
