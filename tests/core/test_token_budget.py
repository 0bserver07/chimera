"""Tests for chimera.core.token_budget — Token Budget Enforcement."""
from __future__ import annotations

from chimera.core.token_budget import (
    COMPLETION_THRESHOLD,
    DIMINISHING_THRESHOLD,
    TokenBudget,
    TokenBudgetResult,
)


class TestTokenBudgetResult:
    """TokenBudgetResult is a simple data container."""

    def test_fields(self):
        r = TokenBudgetResult(should_continue=True, reason="ok")
        assert r.should_continue is True
        assert r.reason == "ok"
        assert r.nudge_message is None

    def test_with_nudge(self):
        r = TokenBudgetResult(should_continue=True, reason="budget_low", nudge_message="wrap up")
        assert r.nudge_message == "wrap up"


class TestTokenBudgetExhausted:
    """Budget is exhausted when usage >= 90% of total budget."""

    def test_exactly_at_threshold(self):
        budget = TokenBudget(1000)
        budget.record(900)  # exactly 90%
        result = budget.check(output_tokens_this_turn=100)
        assert result.should_continue is False
        assert result.reason == "budget_exhausted"

    def test_over_threshold(self):
        budget = TokenBudget(1000)
        budget.record(950)
        result = budget.check(output_tokens_this_turn=100)
        assert result.should_continue is False
        assert result.reason == "budget_exhausted"

    def test_under_threshold_continues(self):
        budget = TokenBudget(1000)
        budget.record(500)
        result = budget.check(output_tokens_this_turn=600)
        assert result.should_continue is True

    def test_zero_budget_exhausted(self):
        budget = TokenBudget(0)
        result = budget.check(output_tokens_this_turn=100)
        assert result.should_continue is False
        assert result.reason == "budget_exhausted"


class TestTokenBudgetDiminishingReturns:
    """Three consecutive low-output turns trigger diminishing returns stop."""

    def test_single_low_output_continues(self):
        budget = TokenBudget(10000)
        budget.record(100)
        result = budget.check(output_tokens_this_turn=10)
        assert result.should_continue is True

    def test_three_consecutive_low_outputs_stops(self):
        budget = TokenBudget(10000)
        budget.record(100)
        budget.check(output_tokens_this_turn=10)
        budget.check(output_tokens_this_turn=10)
        result = budget.check(output_tokens_this_turn=10)
        assert result.should_continue is False
        assert result.reason == "diminishing_returns"

    def test_high_output_resets_counter(self):
        budget = TokenBudget(10000)
        budget.record(100)
        budget.check(output_tokens_this_turn=10)
        budget.check(output_tokens_this_turn=10)
        # High output resets the counter
        budget.check(output_tokens_this_turn=600)
        result = budget.check(output_tokens_this_turn=10)
        # Only 1 consecutive low, should continue
        assert result.should_continue is True


class TestTokenBudgetNudge:
    """When remaining budget is between 10-20%, nudge the model."""

    def test_nudge_when_low(self):
        budget = TokenBudget(1000)
        budget.record(850)  # 15% remaining
        result = budget.check(output_tokens_this_turn=600)
        assert result.should_continue is True
        assert result.reason == "budget_low"
        assert result.nudge_message is not None
        assert "remaining" in result.nudge_message.lower()

    def test_no_nudge_when_plenty(self):
        budget = TokenBudget(1000)
        budget.record(500)  # 50% remaining
        result = budget.check(output_tokens_this_turn=600)
        assert result.should_continue is True
        assert result.reason == "ok"
        assert result.nudge_message is None


class TestTokenBudgetRecord:
    """The record method accumulates usage."""

    def test_accumulates(self):
        budget = TokenBudget(1000)
        budget.record(100)
        budget.record(200)
        assert budget.used == 300

    def test_starts_at_zero(self):
        budget = TokenBudget(1000)
        assert budget.used == 0
