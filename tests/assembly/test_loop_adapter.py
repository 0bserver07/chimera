"""Tests for the loop-swap adapter (real backend heterogeneity, spec §13.3)."""
from types import SimpleNamespace

import pytest

import chimera.assembly.loop_adapter as la
from chimera.core.loop_events import LoopEventType
from chimera.types import AgentResult, Message, StepResult, ToolCall, ToolResult


def test_is_real_loop():
    assert la.is_real_loop("plan-execute")
    assert la.is_real_loop("reflexion")
    assert la.is_real_loop("tot")
    assert not la.is_real_loop("plan")   # a posture, not a real loop
    assert not la.is_real_loop(None)
    assert not la.is_real_loop("")


def test_step_to_events_order_and_payloads():
    step = StepResult(
        message=Message.assistant("thinking"),
        tool_calls=[ToolCall("1", "read_file", {"path": "a.py"})],
        tool_results=[ToolResult(output="contents")],
        step=1,
    )
    evs = la._step_to_events(step, 1)
    assert [e.type for e in evs] == [
        LoopEventType.assistant, LoopEventType.tool_use, LoopEventType.tool_result,
    ]
    assert evs[1].data.name == "read_file"
    assert evs[2].data[0].name == "read_file"
    assert evs[2].data[1].output == "contents"


def test_step_with_no_assistant_text_skips_it():
    step = StepResult(
        message=Message.assistant(""),
        tool_calls=[ToolCall("1", "bash", {})],
        tool_results=[ToolResult(output="ok")],
        step=1,
    )
    assert [e.type for e in la._step_to_events(step, 1)] == [
        LoopEventType.tool_use, LoopEventType.tool_result,
    ]


class _FakeLoop:
    def __init__(self, max_steps=50):
        self.max_steps = max_steps

    def iter_steps(self, provider, tools, context, env):
        yield StepResult(
            message=Message.assistant("reading"),
            tool_calls=[ToolCall("1", "read_file", {})],
            tool_results=[ToolResult(output="x")],
            step=1, cost=0.001,
        )
        yield StepResult(message=Message.assistant("done"), step=2, cost=0.002, done=True)
        return AgentResult(output="done", steps=2, tool_calls_total=1, cost=0.003, success=True)


@pytest.mark.asyncio
async def test_adapt_loop_streams_and_terminates(monkeypatch):
    monkeypatch.setattr(la, "_build_loop", lambda name, steps: _FakeLoop())
    evs = [
        ev async for ev in la.adapt_loop(
            "plan-execute", provider=None, tools=[], system_prompt="s",
            messages=[Message.user("go")],
        )
    ]
    assert [e.type.name for e in evs] == [
        "assistant", "tool_use", "tool_result", "assistant", "result",
    ]
    result = evs[-1].data
    assert result.reason == "completed" and result.turn_count == 2
    assert abs(result.cost_usd - 0.003) < 1e-9


@pytest.mark.asyncio
async def test_adapt_loop_cancels_at_step_boundary(monkeypatch):
    monkeypatch.setattr(la, "_build_loop", lambda name, steps: _FakeLoop())
    evs = [
        ev async for ev in la.adapt_loop(
            "plan-execute", provider=None, tools=[], system_prompt="s",
            messages=[], abort_signal=SimpleNamespace(aborted=True),
        )
    ]
    assert evs[-1].type == LoopEventType.result
    assert evs[-1].data.reason == "cancelled"


@pytest.mark.asyncio
async def test_adapt_loop_error_yields_error_then_terminal_result(monkeypatch):
    class _BoomLoop:
        def __init__(self, max_steps=50):
            pass

        def iter_steps(self, *a):
            raise RuntimeError("boom")

    monkeypatch.setattr(la, "_build_loop", lambda name, steps: _BoomLoop())
    evs = [
        ev async for ev in la.adapt_loop(
            "plan-execute", provider=None, tools=[], system_prompt="s", messages=[],
        )
    ]
    assert LoopEventType.error in [e.type for e in evs]
    assert evs[-1].type == LoopEventType.result and evs[-1].data.reason == "error"


def test_bounded_provider_caps_max_tokens():
    seen = {}

    class _Inner:
        model_name = "glm-5.2"

        def complete(self, messages, tools=None, temperature=0.0, max_tokens=None, **kw):
            seen["max_tokens"] = max_tokens
            return SimpleNamespace(content="ok", tool_calls=[], usage={})

    bounded = la._BoundedProvider(_Inner(), max_tokens=8192)
    assert bounded.model_name == "glm-5.2"          # delegates unknown attrs
    bounded.complete([], tools=None)
    assert seen["max_tokens"] == 8192               # capped when caller passes None
    bounded.complete([], max_tokens=100)
    assert seen["max_tokens"] == 100                # an explicit value is preserved
