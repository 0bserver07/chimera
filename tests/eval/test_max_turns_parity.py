"""T4.4 — budget.max_llm_calls threads into preset max_turns (turn parity)."""

from __future__ import annotations

from chimera.core.budget import BudgetSpec
from chimera.eval.coding_agent_adapter import CodingAgentAdapter
from chimera.eval.runners.in_process import InProcessRunner


def test_adapter_exposes_settable_max_turns() -> None:
    a = CodingAgentAdapter(provider=None, preset="minimal")
    assert a._max_turns is None  # default: preset ceiling
    a.set_max_turns(7)
    assert a._max_turns == 7
    a.set_max_turns(None)  # restore preset default
    assert a._max_turns is None


def test_budgeted_partial_path_aligns_max_turns() -> None:
    """A single-arg preset factory gets its max_turns aligned to the budget.

    The factory returns a CodingAgentAdapter but never runs a live agent here —
    we stub .run to capture that set_max_turns was applied before the run.
    """
    captured: dict[str, int | None] = {}

    class _StubAdapter(CodingAgentAdapter):
        def run(self, task, env):  # type: ignore[override]
            captured["max_turns"] = self._max_turns
            from chimera.types import AgentResult

            return AgentResult(
                output="ok", steps=1, tool_calls_total=0, cost=0.0, success=True
            )

    def factory(provider):  # single-arg → partial budget path
        return _StubAdapter(provider=provider, preset="minimal")

    runner = InProcessRunner(id="minimal", agent_factory=factory, provider=object())
    res = runner.run("do a thing", None, budget=BudgetSpec(max_llm_calls=5))

    assert captured["max_turns"] == 5  # aligned from budget
    assert res.raw["budget_honored"] is False  # honestly still partial
    assert "max_turns aligned to budget.max_llm_calls=5" in res.raw["budget_note"]
