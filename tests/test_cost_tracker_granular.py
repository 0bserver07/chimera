"""Tests for granular token tracking in CostTracker."""
from __future__ import annotations

import pytest

from chimera.events.base import EventBus
from chimera.events.types import StepCostEvent
from chimera.providers.cost_tracker import (
    CostLimitExceeded,
    CostTracker,
    StepUsage,
    TokenUsage,
    _calculate_granular_cost,
)


# ---------------------------------------------------------------------------
# TokenUsage
# ---------------------------------------------------------------------------

class TestTokenUsage:
    def test_defaults(self):
        u = TokenUsage()
        assert u.input_tokens == 0
        assert u.output_tokens == 0
        assert u.cache_read_tokens == 0
        assert u.cache_write_tokens == 0
        assert u.reasoning_tokens == 0
        assert u.cost == 0.0

    def test_cache_hit_rate(self):
        u = TokenUsage(input_tokens=100, cache_read_tokens=80)
        assert u.cache_hit_rate == pytest.approx(80 / 180)

    def test_cache_hit_rate_zero(self):
        u = TokenUsage(input_tokens=0, cache_read_tokens=0)
        assert u.cache_hit_rate == 0.0

    def test_effective_input_tokens(self):
        u = TokenUsage(input_tokens=100, cache_read_tokens=30)
        assert u.effective_input_tokens == 70


# ---------------------------------------------------------------------------
# StepUsage
# ---------------------------------------------------------------------------

class TestStepUsage:
    def test_aggregates_calls(self):
        step = StepUsage(step_index=0)
        step.calls.append(TokenUsage(
            input_tokens=100, output_tokens=50,
            cache_read_tokens=20, cache_write_tokens=10,
            reasoning_tokens=5, cost=0.01,
        ))
        step.calls.append(TokenUsage(
            input_tokens=200, output_tokens=100,
            cache_read_tokens=80, cache_write_tokens=30,
            reasoning_tokens=15, cost=0.02,
        ))
        assert step.total_input_tokens == 300
        assert step.total_output_tokens == 150
        assert step.total_cache_read_tokens == 100
        assert step.total_cache_write_tokens == 40
        assert step.total_reasoning_tokens == 20
        assert step.total_cost == pytest.approx(0.03)

    def test_duration(self):
        step = StepUsage(step_index=0, start_time=10.0, end_time=15.0)
        assert step.duration == 5.0

    def test_empty_step(self):
        step = StepUsage(step_index=0)
        assert step.total_cost == 0.0
        assert step.total_input_tokens == 0


# ---------------------------------------------------------------------------
# CostTracker — backward compatibility
# ---------------------------------------------------------------------------

class TestCostTrackerBackwardCompat:
    def test_record_simple(self):
        tracker = CostTracker(budget=10.0)
        tracker.record(1.50, model="gpt-4o")
        assert tracker.total == pytest.approx(1.50)
        assert tracker.remaining == pytest.approx(8.50)

    def test_budget_exceeded(self):
        tracker = CostTracker(budget=1.0)
        tracker.record(0.5)
        with pytest.raises(CostLimitExceeded):
            tracker.record(0.6)

    def test_breakdown(self):
        tracker = CostTracker()
        tracker.record(1.0, model="a")
        tracker.record(2.0, model="b")
        bd = tracker.breakdown()
        assert bd["a"] == pytest.approx(1.0)
        assert bd["b"] == pytest.approx(2.0)

    def test_reset(self):
        tracker = CostTracker()
        tracker.record(5.0, model="m")
        tracker.reset()
        assert tracker.total == 0.0
        assert tracker.breakdown() == {}

    def test_no_budget(self):
        tracker = CostTracker()
        assert tracker.remaining is None
        tracker.record(1000.0)
        assert tracker.total == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# CostTracker — granular tracking
# ---------------------------------------------------------------------------

class TestCostTrackerGranular:
    def test_record_usage(self):
        tracker = CostTracker()
        usage = tracker.record_usage(
            model="claude-sonnet-4-20250514",
            input_tokens=1000,
            output_tokens=200,
            cache_read_tokens=500,
            cache_write_tokens=100,
            reasoning_tokens=0,
            cost=0.05,
        )
        assert isinstance(usage, TokenUsage)
        assert tracker.total_input_tokens == 1000
        assert tracker.total_output_tokens == 200
        assert tracker.total_cache_read_tokens == 500
        assert tracker.total_cache_write_tokens == 100
        assert tracker.total_cost == pytest.approx(0.05)
        assert tracker.total_calls == 1

    def test_cache_hit_rate(self):
        tracker = CostTracker()
        tracker.record_usage(
            model="m", input_tokens=100, cache_read_tokens=80, cost=0.01,
        )
        # cache_hit_rate = 80 / (100 + 80) = 80/180
        assert tracker.cache_hit_rate == pytest.approx(80 / 180)

    def test_cache_hit_rate_zero(self):
        tracker = CostTracker()
        assert tracker.cache_hit_rate == 0.0

    def test_by_model(self):
        tracker = CostTracker()
        tracker.record_usage(model="a", input_tokens=100, output_tokens=50, cost=1.0)
        tracker.record_usage(model="b", input_tokens=200, output_tokens=100, cost=2.0)
        tracker.record_usage(model="a", input_tokens=50, output_tokens=25, cost=0.5)

        by_model = tracker.by_model
        assert by_model["a"]["calls"] == 2
        assert by_model["a"]["input_tokens"] == 150
        assert by_model["a"]["cost"] == pytest.approx(1.5)
        assert by_model["b"]["calls"] == 1

    def test_budget_enforcement(self):
        tracker = CostTracker(budget=1.0)
        tracker.record_usage(model="m", cost=0.8)
        with pytest.raises(CostLimitExceeded):
            tracker.record_usage(model="m", cost=0.3)

    def test_budget_remaining(self):
        tracker = CostTracker(budget=5.0)
        tracker.record_usage(model="m", cost=2.0)
        assert tracker.budget_remaining == pytest.approx(3.0)

    def test_no_budget_remaining_none(self):
        tracker = CostTracker()
        assert tracker.budget_remaining is None

    def test_context_utilization(self):
        tracker = CostTracker(max_context_tokens=200000)
        tracker.record_usage(model="m", cost=0.01, context_tokens=160000)
        assert tracker.context_utilization == pytest.approx(0.8)

    def test_context_utilization_no_max(self):
        tracker = CostTracker()
        assert tracker.context_utilization == 0.0

    def test_on_usage_update_callback(self):
        received = []
        tracker = CostTracker(on_usage_update=received.append)
        tracker.record_usage(model="m", input_tokens=100, output_tokens=50, cost=0.01)
        assert len(received) == 1
        assert received[0].input_tokens == 100


# ---------------------------------------------------------------------------
# Step tracking
# ---------------------------------------------------------------------------

class TestStepTracking:
    def test_start_end_step(self):
        tracker = CostTracker()
        tracker.start_step(0)
        tracker.record_usage(model="m", input_tokens=100, output_tokens=50, cost=0.05)
        tracker.record_usage(model="m", input_tokens=200, output_tokens=100, cost=0.10)
        step = tracker.end_step()

        assert step is not None
        assert step.step_index == 0
        assert len(step.calls) == 2
        assert step.total_cost == pytest.approx(0.15)
        assert step.total_input_tokens == 300

    def test_steps_list(self):
        tracker = CostTracker()
        for i in range(3):
            tracker.start_step(i)
            tracker.record_usage(model="m", cost=float(i + 1))
            tracker.end_step()

        assert len(tracker.steps) == 3
        assert tracker.steps[0].step_index == 0
        assert tracker.steps[2].step_index == 2

    def test_most_expensive_step(self):
        tracker = CostTracker()
        for i, cost in enumerate([0.5, 2.0, 0.3]):
            tracker.start_step(i)
            tracker.record_usage(model="m", cost=cost)
            tracker.end_step()

        expensive = tracker.most_expensive_step()
        assert expensive is not None
        assert expensive.step_index == 1

    def test_most_expensive_step_empty(self):
        tracker = CostTracker()
        assert tracker.most_expensive_step() is None

    def test_end_step_without_start(self):
        tracker = CostTracker()
        assert tracker.end_step() is None

    def test_usage_outside_step(self):
        tracker = CostTracker()
        tracker.record_usage(model="m", cost=1.0)
        assert tracker.total_cost == pytest.approx(1.0)
        assert len(tracker.steps) == 0


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class TestSummary:
    def test_summary_keys(self):
        tracker = CostTracker(budget=10.0, max_context_tokens=200000)
        tracker.record_usage(
            model="claude-sonnet-4",
            input_tokens=1000, output_tokens=200,
            cache_read_tokens=500, cache_write_tokens=100,
            reasoning_tokens=50, cost=0.05,
            context_tokens=100000,
        )
        tracker.start_step(0)
        tracker.record_usage(model="claude-sonnet-4", cost=0.01)
        tracker.end_step()

        s = tracker.summary()
        assert s["total_cost"] == pytest.approx(0.06)
        assert s["total_calls"] == 2
        assert s["total_input_tokens"] == 1000
        assert s["total_output_tokens"] == 200
        assert s["total_cache_read_tokens"] == 500
        assert s["total_cache_write_tokens"] == 100
        assert s["total_reasoning_tokens"] == 50
        assert s["budget"] == 10.0
        assert s["budget_remaining"] == pytest.approx(9.94)
        assert s["steps"] == 1
        assert s["most_expensive_step"] == 0
        assert "cache_hit_rate" in s
        assert "context_utilization" in s
        assert "by_model" in s

    def test_summary_no_steps(self):
        tracker = CostTracker()
        s = tracker.summary()
        assert s["steps"] == 0
        assert s["most_expensive_step"] is None


# ---------------------------------------------------------------------------
# Granular cost calculation
# ---------------------------------------------------------------------------

class TestGranularCostCalculation:
    def test_basic_calculation(self):
        cost = _calculate_granular_cost("claude-sonnet-4-20250514", 1000, 100)
        assert cost > 0

    def test_cache_discount(self):
        cost_no_cache = _calculate_granular_cost("claude-sonnet-4", 1000, 100)
        cost_with_cache = _calculate_granular_cost(
            "claude-sonnet-4", 1000, 100, cache_read_tokens=800,
        )
        assert cost_with_cache < cost_no_cache

    def test_reasoning_tokens(self):
        cost_no_reasoning = _calculate_granular_cost("o1", 1000, 100)
        cost_with_reasoning = _calculate_granular_cost(
            "o1", 1000, 100, reasoning_tokens=500,
        )
        assert cost_with_reasoning > cost_no_reasoning

    def test_unknown_model_uses_default(self):
        cost = _calculate_granular_cost("unknown-model-xyz", 1000, 100)
        assert cost > 0

    def test_auto_cost_calculation(self):
        tracker = CostTracker()
        usage = tracker.record_usage(
            model="claude-sonnet-4", input_tokens=1000, output_tokens=100,
        )
        assert usage.cost > 0


# ---------------------------------------------------------------------------
# StepCostEvent
# ---------------------------------------------------------------------------

class TestStepCostEvent:
    def test_event_creation(self):
        event = StepCostEvent(
            step_index=3,
            cost=0.15,
            input_tokens=1000,
            output_tokens=200,
            reasoning_tokens=50,
            cache_hit_rate=0.75,
            duration=2.5,
        )
        assert event.type == "step_cost"
        assert event.step_index == 3
        assert event.cost == 0.15

    def test_event_bus_emission(self):
        bus = EventBus()
        received = []
        bus.subscribe("step_cost", lambda e: received.append(e))

        event = StepCostEvent(step_index=0, cost=0.05)
        bus.publish(event)

        assert len(received) == 1
        assert received[0].step_index == 0


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

class TestReset:
    def test_full_reset(self):
        tracker = CostTracker(budget=10.0, max_context_tokens=200000)
        tracker.record_usage(
            model="m", input_tokens=100, output_tokens=50,
            cache_read_tokens=20, reasoning_tokens=10,
            cost=0.05, context_tokens=50000,
        )
        tracker.start_step(0)
        tracker.record_usage(model="m", cost=0.01)
        tracker.end_step()

        tracker.reset()

        assert tracker.total == 0.0
        assert tracker.total_input_tokens == 0
        assert tracker.total_output_tokens == 0
        assert tracker.total_cache_read_tokens == 0
        assert tracker.total_cache_write_tokens == 0
        assert tracker.total_reasoning_tokens == 0
        assert tracker.total_cost == 0.0
        assert tracker.total_calls == 0
        assert len(tracker.steps) == 0
        assert tracker.by_model == {}
        assert tracker.context_utilization == 0.0
