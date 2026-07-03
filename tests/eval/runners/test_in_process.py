"""Plumbing tests for InProcessRunner (no LLM required)."""

from __future__ import annotations

import pytest

from chimera.eval.runners import AgentRunner, AgentRunResult, InProcessRunner
from chimera.types import AgentResult


class _FakeAgent:
    """Minimal agent satisfying the Harness ``run(prompt, env)`` contract."""

    def __init__(self, result: AgentResult) -> None:
        self.result = result
        self.calls: list[tuple[str, object]] = []

    def run(self, prompt: str, env: object = None) -> AgentResult:
        self.calls.append((prompt, env))
        return self.result


def _ok_result() -> AgentResult:
    return AgentResult(
        output="the answer",
        steps=3,
        tool_calls_total=5,
        cost=0.0125,
        success=True,
    )


def test_maps_native_result_onto_run_result() -> None:
    agent = _FakeAgent(_ok_result())
    runner = InProcessRunner("fake", agent=agent)

    out = runner.run({"id": "t1", "prompt": "do the thing"})

    assert isinstance(out, AgentRunResult)
    assert out.answer == "the answer"
    assert out.tool_calls == 5
    assert out.llm_calls == 3
    assert out.cost_usd == pytest.approx(0.0125)
    assert out.status == "completed"
    # prompt was extracted from the task dict
    assert agent.calls == [("do the thing", None)]


def test_runtime_checkable_protocol_and_lazy_factory() -> None:
    made: dict[str, object] = {}

    def factory(provider: object) -> _FakeAgent:
        made["provider"] = provider
        return _FakeAgent(_ok_result())

    runner = InProcessRunner("f", agent_factory=factory, provider="PROVIDER")
    assert isinstance(runner, AgentRunner)  # runtime_checkable
    assert "provider" not in made  # not constructed yet (lazy)

    out = runner.run("a raw string prompt")
    assert out.status == "completed"
    assert made["provider"] == "PROVIDER"


def test_error_result_maps_to_error_status() -> None:
    err = AgentResult(
        output="",
        steps=1,
        tool_calls_total=0,
        cost=0.0,
        success=False,
        error="boom",
    )
    runner = InProcessRunner("e", agent=_FakeAgent(err))

    out = runner.run("x")

    assert out.status == "error"
    assert out.raw["error"] == "boom"


def test_requires_agent_or_factory() -> None:
    with pytest.raises(ValueError, match="agent"):
        InProcessRunner("bad")
