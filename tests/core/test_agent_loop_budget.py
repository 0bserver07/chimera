"""Budget enforcement through the real AgentLoop / assembled stack (#170).

Hermetic: a scripted :class:`~chimera.providers.faux.FauxProvider` drives the
untouched production loop via the testing harness, so these lock the actual
integration (records + boundary checks + terminal reason), not a mock. No
``tui`` extra is involved — this is the core seam every lane reuses.
"""
from __future__ import annotations

import chimera.core.budget as budget_mod
from chimera.core.budget import BudgetEnforcer, BudgetSpec
from chimera.testing import create_assembled_harness, create_harness

# A step that keeps the loop going by calling a real (workspace-scoped) tool.
_ECHO = {"tool_calls": [{"name": "bash", "arguments": {"command": "echo hi"}}]}


class TestAgentLoopBudget:
    def test_steps_cap_stops_with_llm_calls_reason(self) -> None:
        enforcer = BudgetEnforcer(BudgetSpec(max_llm_calls=2))
        run = create_harness(
            [_ECHO, _ECHO, _ECHO, {"text": "done"}],
            model="glm-5.2",
            max_turns=10,
            config={"budget_enforcer": enforcer},
        ).run("go")
        assert run.reason == "budget_exhausted:llm_calls"
        assert enforcer.tally.llm_calls == 2  # the tipping call finished; no more

    def test_tool_calls_cap_stops_with_tool_calls_reason(self) -> None:
        enforcer = BudgetEnforcer(BudgetSpec(max_tool_calls=1))
        run = create_harness(
            [_ECHO, _ECHO, {"text": "done"}],
            model="glm-5.2",
            max_turns=10,
            config={"budget_enforcer": enforcer},
        ).run("go")
        assert run.reason == "budget_exhausted:tool_calls"
        assert enforcer.tally.tool_calls == 1

    def test_cost_cap_stops_with_cost_reason(self) -> None:
        # 1M input tokens on glm-5.2 prices well over a 1-cent cap.
        enforcer = BudgetEnforcer(BudgetSpec(max_cost_usd=0.01))
        run = create_harness(
            [{"tool_calls": [{"name": "bash", "arguments": {"command": "echo hi"}}],
              "usage": {"input_tokens": 1_000_000, "output_tokens": 10}},
             {"text": "done"}],
            model="glm-5.2",
            max_turns=10,
            config={"budget_enforcer": enforcer},
        ).run("go")
        assert run.reason == "budget_exhausted:cost"
        assert enforcer.tally.cost_usd > 0.01

    def test_wall_clock_cap_stops_with_wall_clock_reason(self, monkeypatch) -> None:
        clock = {"now": 0.0}
        monkeypatch.setattr(budget_mod.time, "monotonic", lambda: clock["now"])
        enforcer = BudgetEnforcer(BudgetSpec(max_wall_clock_sec=5.0))
        enforcer.start()          # the caller owns start/pause; here we pre-arm it
        clock["now"] = 10.0       # already over the cap before the first model call
        run = create_harness(
            [{"text": "should never run"}],
            model="glm-5.2",
            config={"budget_enforcer": enforcer},
        ).run("go")
        assert run.reason == "budget_exhausted:wall_clock"

    def test_no_budget_is_unchanged(self) -> None:
        # Byte-identical shape: with no enforcer the run completes normally and
        # its event sequence matches a baseline run with the arg omitted.
        script = [_ECHO, {"text": "done"}]
        baseline = create_harness(script, model="glm-5.2", max_turns=10).run("go")
        with_arg = create_harness(
            script, model="glm-5.2", max_turns=10, config={"budget_enforcer": None},
        ).run("go")
        assert baseline.reason == "completed"
        assert with_arg.reason == "completed"
        assert baseline.event_types == with_arg.event_types


class TestAssembledBudget:
    def test_budget_flows_through_coding_agent(self) -> None:
        run = create_assembled_harness(
            [{"tool_calls": [{"name": "bash", "arguments": {"command": "echo hi"}}],
              "usage": {"input_tokens": 1_000_000, "output_tokens": 10}},
             {"text": "done"}],
            model="glm-5.2",
            max_turns=10,
            agent_kwargs={"budget": BudgetSpec(max_cost_usd=0.01)},
        ).run("go")
        assert run.reason == "budget_exhausted:cost"

    def test_all_none_spec_is_treated_as_no_budget(self) -> None:
        run = create_assembled_harness(
            [{"text": "hello"}],
            model="glm-5.2",
            agent_kwargs={"budget": BudgetSpec()},  # is_set is False
        ).run("hi")
        assert run.reason == "completed"

    def test_cost_accumulates_across_turns(self) -> None:
        # ONE enforcer for the agent's life: turn 1 stays under a $3 cap and
        # completes; turn 2 pushes cumulative cost over it and trips. Proves the
        # lane's budget spans successive turns, not just one.
        harness = create_assembled_harness(
            [{"text": "turn one", "usage": {"input_tokens": 1_000_000, "output_tokens": 10}},
             {"text": "turn two", "usage": {"input_tokens": 1_000_000, "output_tokens": 10}}],
            model="glm-5.2",
            max_turns=10,
            agent_kwargs={"budget": BudgetSpec(max_cost_usd=3.0)},
        )
        first = harness.run("go")
        assert first.reason == "completed"
        second = harness.run("again")
        assert second.reason == "budget_exhausted:cost"
