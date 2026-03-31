"""Backward-compatibility tests for ReAct: verifies that existing methods are
untouched and that the new async_run_events() method delegates to AgentLoop."""
from __future__ import annotations

import pytest

from chimera.core.context import Context
from chimera.core.loop import ReAct
from chimera.core.loop_events import LoopEvent, LoopEventType
from chimera.core.tool import BaseTool
from chimera.providers.base import Response
from chimera.types import AgentResult, Message, ToolResult


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class MockProvider:
    """Minimal mock provider that returns a single canned response."""

    model_name = "mock"

    def __init__(self, response: Response) -> None:
        self._response = response

    def complete(self, messages, tools=None, **kwargs):
        return self._response

    async def async_complete(self, messages, tools=None, **kwargs):
        return self._response


def _simple_response() -> Response:
    return Response(content="All done!", tool_calls=[], usage={})


def _make_context() -> Context:
    ctx = Context(system="You are helpful.")
    ctx.add(Message.user("Hello"))
    return ctx


# ---------------------------------------------------------------------------
# Test 1: existing run() still works and returns AgentResult
# ---------------------------------------------------------------------------


def test_existing_run_still_works():
    """ReAct.run() must still return an AgentResult (backwards compat)."""
    provider = MockProvider(_simple_response())
    react = ReAct(max_steps=5)
    result = react.run(provider=provider, tools=[], context=_make_context(), env=None)

    assert isinstance(result, AgentResult), (
        f"Expected AgentResult, got {type(result)}"
    )
    assert result.output == "All done!"
    assert result.success is True
    assert result.steps == 1


# ---------------------------------------------------------------------------
# Test 2: async_run_events() yields LoopEvents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_run_events_yields_loop_events():
    """ReAct.async_run_events() must yield at least one LoopEvent with
    type == result and reason == 'completed'."""
    provider = MockProvider(_simple_response())
    react = ReAct(max_steps=5)

    events: list[LoopEvent] = []
    async for event in react.async_run_events(
        provider=provider,
        tools=[],
        context=_make_context(),
        env=None,
    ):
        events.append(event)

    assert len(events) > 0, "async_run_events() yielded no events"
    assert all(isinstance(e, LoopEvent) for e in events), (
        "Not all yielded items are LoopEvent instances"
    )
    result_events = [e for e in events if e.type == LoopEventType.result]
    assert len(result_events) == 1, (
        f"Expected exactly one result event, got {len(result_events)}"
    )
    assert result_events[0].data.reason == "completed"
