"""Wire tests for InProcessRunner per-task budget enforcement (no LLM/network).

These prove the matrix runner honors a :class:`~chimera.core.budget.BudgetSpec`
the same way ``ComparativeEval.run_with_budget`` does: a budget-aware factory
(one that accepts a ``loop_config``) gets full tool-call enforcement and stops
at the cap with ``status="budget_exhausted"``; a single-argument factory still
runs but is honestly flagged ``budget_honored=False``; and the unbudgeted path
is byte-for-byte unchanged. A scripted provider that always proposes one more
tool call stands in for a real model, so nothing here touches the network.
"""

from __future__ import annotations

import itertools
from typing import Any

from chimera.core.agent import Agent
from chimera.core.budget import BudgetSpec
from chimera.core.loop import ReAct
from chimera.core.tool import BaseTool
from chimera.eval.runners.in_process import InProcessRunner
from chimera.providers.base import Provider, Response
from chimera.types import AgentResult, Message, ToolCall, ToolResult


class PingTool(BaseTool):
    """Trivial always-succeeds tool so tool calls actually execute."""

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


class FinishesProvider(AlwaysToolProvider):
    """Scripted provider that answers immediately with no tool call."""

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
            content="FINAL: 42",
            tool_calls=[],
            usage={"input_tokens": 5, "output_tokens": 3},
        )


def _budget_aware_factory(provider: Any, loop_config: Any = None) -> Agent:
    """Two-arg factory: threads ``loop_config`` into ReAct (full enforcement)."""
    loop = (
        ReAct(max_steps=20, config=loop_config)
        if loop_config is not None
        else ReAct(max_steps=20)
    )
    return Agent(provider=provider, tools=[PingTool()], loop=loop)


def _partial_factory(provider: Any) -> Agent:
    """Single-arg factory: no config seam, so the budget is only partial."""
    return Agent(provider=provider, tools=[PingTool()], loop=ReAct(max_steps=4))


class _FakeAgent:
    """Minimal agent satisfying the Harness ``run(prompt, env)`` contract."""

    def __init__(self, result: AgentResult) -> None:
        self.result = result
        self.calls: list[tuple[str, object]] = []

    def run(self, prompt: str, env: object = None) -> AgentResult:
        self.calls.append((prompt, env))
        return self.result


# --------------------------------------------------------------------------- #
# Full tool-call enforcement (budget-aware factory)
# --------------------------------------------------------------------------- #
def test_budget_aware_factory_stops_at_tool_call_cap() -> None:
    runner = InProcessRunner(
        "react", agent_factory=_budget_aware_factory, provider=AlwaysToolProvider()
    )

    out = runner.run({"id": "t1", "prompt": "keep pinging"}, budget=BudgetSpec(max_tool_calls=2))

    assert out.status == "budget_exhausted"
    assert out.tool_calls == 2, f"expected exactly 2 tool calls, got {out.tool_calls}"
    assert out.raw["budget_honored"] is True
    assert out.raw["budget_note"] == ""
    assert str(out.raw["budget_reason"]).startswith("tool_calls")


def test_budget_aware_factory_completes_under_a_generous_cap() -> None:
    # The agent answers on step 1, well under the cap -> a normal completion,
    # still flagged honored=True (the runner *could* have enforced).
    runner = InProcessRunner(
        "react", agent_factory=_budget_aware_factory, provider=FinishesProvider()
    )

    out = runner.run("solve it", budget=BudgetSpec(max_tool_calls=10))

    assert out.status == "completed"
    assert out.tool_calls == 0
    assert out.raw["budget_honored"] is True
    assert out.raw["budget_note"] == ""


# --------------------------------------------------------------------------- #
# Partial support (single-arg factory) — honest, not faked
# --------------------------------------------------------------------------- #
def test_partial_factory_reports_not_honored() -> None:
    runner = InProcessRunner(
        "partial", agent_factory=_partial_factory, provider=AlwaysToolProvider()
    )

    out = runner.run("keep pinging", budget=BudgetSpec(max_tool_calls=2))

    # The single-arg factory cannot receive the loop_config, so the tool-call
    # budget is NOT enforced (the loop runs to its own max_steps=4 ceiling).
    assert out.raw["budget_honored"] is False
    assert out.raw["budget_note"] == "factory does not accept loop_config"
    assert out.status != "budget_exhausted"


def test_partial_path_still_enforces_provider_level_llm_cap() -> None:
    # Even without a config seam, the provider is wrapped in a BudgetedProvider,
    # so an LLM-call cap still trips — this is the honest "partial" enforcement.
    runner = InProcessRunner(
        "partial", agent_factory=_partial_factory, provider=AlwaysToolProvider()
    )

    out = runner.run("keep pinging", budget=BudgetSpec(max_llm_calls=2))

    assert out.status == "budget_exhausted"
    assert str(out.raw["budget_reason"]).startswith("llm_calls")
    assert out.raw["budget_honored"] is False  # tool-call unit still unenforceable


def test_ready_agent_cannot_be_budgeted() -> None:
    # A runner built from a ready agent (no factory) has no seam to inject a
    # budget, so it runs but is flagged not-honored.
    ok = AgentResult(output="done", steps=1, tool_calls_total=0, cost=0.0, success=True)
    runner = InProcessRunner("ready", agent=_FakeAgent(ok))

    out = runner.run("x", budget=BudgetSpec(max_tool_calls=1))

    assert out.raw["budget_honored"] is False
    assert out.raw["budget_note"] == "factory does not accept loop_config"
    assert out.status == "completed"


# --------------------------------------------------------------------------- #
# Unbudgeted path is unchanged (no budget flags leak in)
# --------------------------------------------------------------------------- #
def test_unbudgeted_run_is_unchanged() -> None:
    ok = AgentResult(
        output="the answer", steps=3, tool_calls_total=5, cost=0.0125, success=True
    )
    agent = _FakeAgent(ok)
    runner = InProcessRunner("fake", agent=agent)

    out = runner.run({"id": "t1", "prompt": "do the thing"})

    assert out.status == "completed"
    assert out.answer == "the answer"
    assert out.tool_calls == 5
    assert out.llm_calls == 3
    # No budget machinery ran, so no budget flags are attached.
    assert "budget_honored" not in out.raw
    assert "budget_note" not in out.raw
    assert agent.calls == [("do the thing", None)]


def test_budgeted_run_does_not_poison_the_unbudgeted_cache() -> None:
    # A budgeted run must build a throwaway agent, never the cached one, so a
    # later unbudgeted run still gets a clean lazily-built agent.
    builds: list[int] = []

    def counting_factory(provider: Any, loop_config: Any = None) -> Agent:
        builds.append(1)
        return _budget_aware_factory(provider, loop_config)

    runner = InProcessRunner(
        "react", agent_factory=counting_factory, provider=AlwaysToolProvider()
    )

    budgeted = runner.run("keep pinging", budget=BudgetSpec(max_tool_calls=2))
    assert budgeted.status == "budget_exhausted"

    # Unbudgeted afterward: a fresh agent is lazily built and cached, and it is
    # not the (budget-tripped) throwaway from the first call.
    first = runner.run("keep pinging")
    second = runner.run("keep pinging")
    assert "budget_honored" not in first.raw
    assert "budget_honored" not in second.raw
    # 1 build for the budgeted throwaway + 1 build for the first unbudgeted run;
    # the second unbudgeted run reuses the cache and adds no build.
    assert sum(builds) == 2
