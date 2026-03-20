"""Tests for thinking level abstraction."""
from chimera.providers.thinking import ThinkingLevel, budget_for_level, THINKING_BUDGETS


def test_thinking_levels_enum():
    assert ThinkingLevel.OFF == "off"
    assert ThinkingLevel.MAX == "max"
    assert len(ThinkingLevel) == 6


def test_budget_for_level():
    assert budget_for_level(ThinkingLevel.OFF) == 0
    assert budget_for_level(ThinkingLevel.MINIMAL) == 1024
    assert budget_for_level(ThinkingLevel.MAX) == 32768


def test_all_levels_have_budgets():
    for level in ThinkingLevel:
        assert level in THINKING_BUDGETS


def test_budgets_increase():
    levels = [ThinkingLevel.OFF, ThinkingLevel.MINIMAL, ThinkingLevel.LOW,
              ThinkingLevel.MEDIUM, ThinkingLevel.HIGH, ThinkingLevel.MAX]
    budgets = [budget_for_level(l) for l in levels]
    assert budgets == sorted(budgets)
