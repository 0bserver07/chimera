"""Tests for CostTracker and estimate_cost."""
from __future__ import annotations

from typing import Any

import pytest

from chimera.providers.cost_tracker import CostLimitExceeded, CostTracker
from chimera.providers.cost import estimate_cost


class TestCostTracker:
    def test_record_and_total(self) -> None:
        """record() accumulates cost, total reflects it."""
        tracker = CostTracker()
        tracker.record(0.05, model="gpt-4o")
        tracker.record(0.10, model="claude-sonnet-4")
        assert abs(tracker.total - 0.15) < 1e-9

    def test_budget_enforcement(self) -> None:
        """Exceeding budget raises CostLimitExceeded."""
        tracker = CostTracker(budget=0.10)
        tracker.record(0.05)
        with pytest.raises(CostLimitExceeded):
            tracker.record(0.06)

    def test_remaining(self) -> None:
        """remaining reflects budget minus spent."""
        tracker = CostTracker(budget=1.0)
        tracker.record(0.30)
        assert abs(tracker.remaining - 0.70) < 1e-9

    def test_remaining_no_budget(self) -> None:
        """remaining is None when no budget set."""
        tracker = CostTracker()
        assert tracker.remaining is None

    def test_breakdown_by_model(self) -> None:
        """breakdown() returns per-model costs."""
        tracker = CostTracker()
        tracker.record(0.05, model="gpt-4o")
        tracker.record(0.10, model="gpt-4o")
        tracker.record(0.20, model="claude-sonnet-4")

        bd = tracker.breakdown()
        assert abs(bd["gpt-4o"] - 0.15) < 1e-9
        assert abs(bd["claude-sonnet-4"] - 0.20) < 1e-9

    def test_reset(self) -> None:
        """reset() clears total and breakdown."""
        tracker = CostTracker(budget=1.0)
        tracker.record(0.50, model="gpt-4o")
        tracker.reset()
        assert tracker.total == 0.0
        assert tracker.breakdown() == {}
        assert abs(tracker.remaining - 1.0) < 1e-9

    def test_empty_model(self) -> None:
        """Recording with empty model string works."""
        tracker = CostTracker()
        tracker.record(0.05)
        assert abs(tracker.total - 0.05) < 1e-9
        assert "" in tracker.breakdown()


class TestEstimateCost:
    def test_known_model(self) -> None:
        """estimate_cost returns correct value for known model."""
        cost = estimate_cost("claude-sonnet-4", input_tokens=1000, output_tokens=500)
        expected = (1000 * 3.0 + 500 * 15.0) / 1_000_000
        assert abs(cost - expected) < 1e-9

    def test_unknown_model(self) -> None:
        """estimate_cost returns 0.0 for unknown model."""
        cost = estimate_cost("unknown-model", input_tokens=1000, output_tokens=500)
        assert cost == 0.0


class TestCostTrackerInLoop:
    @pytest.mark.asyncio
    async def test_budget_stops_loop(self) -> None:
        """CostTracker budget stops the ReAct loop."""
        from chimera.core.loop import ReAct
        from chimera.core.loop_config import LoopConfig
        from chimera.core.context import Context
        from chimera.providers.base import Provider, Response
        from chimera.types import Message, ToolCall, ToolResult
        from chimera.core.tool import BaseTool

        class ExpensiveProvider(Provider):
            def __init__(self) -> None:
                self._idx = 0

            def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
                self._idx += 1
                return Response(
                    content=f"step {self._idx}",
                    tool_calls=[ToolCall(id=f"tc{self._idx}", name="echo", arguments={"msg": "x"})],
                    usage={"input_tokens": 100_000, "output_tokens": 50_000},
                )

            async def async_complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
                return self.complete(messages, tools, temperature, max_tokens)

            @property
            def context_window(self) -> int:
                return 200_000

            @property
            def supports_tool_use(self) -> bool:
                return True

            @property
            def model_name(self) -> str:
                return "claude-sonnet-4"

        class EchoTool(BaseTool):
            name = "echo"
            description = "echo"
            parameters: dict[str, Any] = {
                "type": "object",
                "properties": {"msg": {"type": "string"}},
                "required": ["msg"],
            }

            def execute(self, args, env=None):
                return ToolResult(output=f"echo:{args['msg']}")

        tracker = CostTracker(budget=0.01)
        config = LoopConfig(cost_tracker=tracker)
        loop = ReAct(max_steps=100, config=config)
        context = Context(system="test")
        context.add(Message.user("go"))

        result = await loop.async_run(ExpensiveProvider(), [EchoTool()], context, None)
        assert result.success is False
        assert "cost" in result.error.lower()
