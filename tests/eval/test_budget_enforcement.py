"""Cross-loop budget-enforcement audit.

The comparative matrix's "controlled" claim requires the budget unit
(completed tool calls) to be enforced identically for every loop type,
regardless of each loop's native notion of a step. These tests script a
provider that *always* proposes another tool call, set
``max_tool_calls=3``, and assert each loop stops with exactly 3
completed calls — proving enforcement happens at the shared
tool-executor choke point, not per-loop.
"""
from __future__ import annotations

import itertools
from typing import Any

import pytest

from chimera.core.agent import Agent
from chimera.core.budget import BudgetEnforcer, BudgetSpec
from chimera.core.cancellation import CancellationToken, OperationCancelled
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.core.loops.plan_execute import PlanAndExecute
from chimera.core.loops.reflexion import Reflexion
from chimera.core.loops.tree_of_thought import TreeOfThought
from chimera.core.tool import BaseTool
from chimera.permissions.presets import AutoApprove
from chimera.providers.base import Provider, Response
from chimera.types import Message, ToolCall, ToolResult


class PingTool(BaseTool):
    name = "ping"
    description = "Returns pong."
    parameters = {"type": "object", "properties": {}}

    def execute(self, args: dict[str, Any], env: Any) -> ToolResult:
        return ToolResult(output="pong")


class AlwaysToolProvider(Provider):
    """Scripted provider: every completion proposes one more tool call."""

    def __init__(self) -> None:
        self._ids = itertools.count()

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any | None = None,
        cancel_event: Any | None = None,
        **kwargs: Any,
    ) -> Response:
        return Response(
            content="calling ping",
            tool_calls=[ToolCall(id=f"tc-{next(self._ids)}", name="ping", arguments={})],
            usage={"input_tokens": 10, "output_tokens": 5},
        )

    @property
    def context_window(self) -> int:
        return 8192

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "scripted-always-tool"


def _budgeted_config(max_tool_calls: int) -> tuple[LoopConfig, BudgetEnforcer]:
    token = CancellationToken()
    enforcer = BudgetEnforcer(
        BudgetSpec(max_tool_calls=max_tool_calls), cancellation=token
    )
    config = LoopConfig(
        budget_enforcer=enforcer,
        cancellation=token,
        permissions=AutoApprove(),
    )
    return config, enforcer


@pytest.mark.parametrize(
    "loop_cls",
    [ReAct, PlanAndExecute, Reflexion, TreeOfThought],
    ids=["react", "plan-execute", "reflexion", "tree-of-thought"],
)
def test_every_loop_type_stops_at_the_tool_call_budget(loop_cls) -> None:
    config, enforcer = _budgeted_config(max_tool_calls=3)
    loop = loop_cls(max_steps=20, config=config)
    agent = Agent(
        provider=AlwaysToolProvider(), tools=[PingTool()], loop=loop
    )

    try:
        agent.run("keep pinging", None)
    except OperationCancelled:
        pass  # loops without their own catch surface the cooperative cancel

    assert enforcer.exhausted, f"{loop_cls.__name__} never tripped the budget"
    assert enforcer.exhausted_reason is not None
    assert enforcer.exhausted_reason.startswith("tool_calls")
    assert enforcer.tally.tool_calls == 3, (
        f"{loop_cls.__name__} executed {enforcer.tally.tool_calls} tool calls "
        "under a 3-call budget"
    )


def test_react_without_budget_runs_to_its_step_ceiling() -> None:
    # Control: no enforcer -> the only stop is the loop's native max_steps.
    config = LoopConfig(permissions=AutoApprove())
    loop = ReAct(max_steps=5, config=config)
    agent = Agent(provider=AlwaysToolProvider(), tools=[PingTool()], loop=loop)
    result = agent.run("keep pinging", None)
    assert result.steps == 5


def test_budget_hit_is_distinct_from_failure_in_compare_report() -> None:
    from chimera.eval.comparative import ComparativeEval

    problems = [{"id": "p1", "prompt": "keep pinging", "expected": "never-matches"}]
    comp = ComparativeEval(AlwaysToolProvider(), problems)

    def factory(provider: Any, loop_config: Any) -> Agent:
        return Agent(
            provider=provider,
            tools=[PingTool()],
            loop=ReAct(max_steps=20, config=loop_config),
        )

    comp.add_config("react", factory)
    report = comp.run_with_budget(
        BudgetSpec(max_tool_calls=2), model="scripted", task_pool="unit", seed=0
    )

    assert report.budget_hits["react"] == 1
    assert report.budget_reasons["react"][0].startswith("tool_calls")
    assert report.results["react"][0].passed is False
    assert "budget_hits=1/1" in report.summary()


def test_llm_call_budget_enforced_at_provider_level() -> None:
    from chimera.eval.comparative import ComparativeEval

    problems = [{"id": "p1", "prompt": "keep pinging"}]
    comp = ComparativeEval(AlwaysToolProvider(), problems)

    def factory(provider: Any, loop_config: Any) -> Agent:
        return Agent(
            provider=provider,
            tools=[PingTool()],
            loop=ReAct(max_steps=20, config=loop_config),
        )

    comp.add_config("react", factory)
    report = comp.run_with_budget(BudgetSpec(max_llm_calls=2), model="scripted")
    assert report.budget_hits["react"] == 1
    assert report.budget_reasons["react"][0].startswith("llm_calls")


def test_agent_crash_is_a_task_failure_not_a_harness_failure() -> None:
    from chimera.eval.comparative import ComparativeEval

    class _BoomAgent:
        def run(self, task: Any, env: Any) -> Any:
            raise AssertionError("tool blew up")

    problems = [{"id": "p1", "prompt": "x"}, {"id": "p2", "prompt": "y"}]
    comp = ComparativeEval(AlwaysToolProvider(), problems)
    comp.add_config("boom", lambda provider, loop_config: _BoomAgent())

    def ok_factory(provider: Any, loop_config: Any) -> Agent:
        return Agent(
            provider=provider,
            tools=[PingTool()],
            loop=ReAct(max_steps=20, config=loop_config),
        )

    comp.add_config("react", ok_factory)
    report = comp.run_with_budget(BudgetSpec(max_tool_calls=2), model="scripted")

    boom = report.results["boom"]
    assert [r.passed for r in boom] == [False, False]
    assert all("[agent error: AssertionError" in r.output for r in boom)
    assert report.budget_hits["boom"] == 0  # crash is not a budget hit
    # the healthy config still ran and recorded its budget hits
    assert report.budget_hits["react"] == 2


def test_same_budget_same_tasks_reproduces_identical_matrix() -> None:
    from chimera.eval.comparative import ComparativeEval

    problems = [{"id": f"p{i}", "prompt": "ping"} for i in range(3)]

    def run_once() -> Any:
        comp = ComparativeEval(AlwaysToolProvider(), problems)

        def factory(provider: Any, loop_config: Any) -> Agent:
            return Agent(
                provider=provider,
                tools=[PingTool()],
                loop=ReAct(max_steps=20, config=loop_config),
            )

        comp.add_config("react", factory)
        return comp.run_with_budget(BudgetSpec(max_tool_calls=2), seed=0)

    a, b = run_once(), run_once()
    assert a.budget_hits == b.budget_hits
    assert [(r.problem_id, r.passed, r.steps) for r in a.results["react"]] == [
        (r.problem_id, r.passed, r.steps) for r in b.results["react"]
    ]
