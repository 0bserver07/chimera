"""Tests for the CodingAgent -> Harness adapter (event aggregation)."""

from __future__ import annotations

import asyncio
from typing import Any

from chimera.core.loop_events import LoopEvent, LoopEventType
from chimera.eval.coding_agent_adapter import (
    CodingAgentAdapter,
    _last_assistant_text,
    aggregate_events,
)
from chimera.types import AgentResult, Message


async def _stream(events: list[LoopEvent]) -> Any:
    for e in events:
        yield e


class _Result:
    """Stand-in for the loop's terminal ``result`` event payload."""

    def __init__(self, cost_usd: float, turn_count: int, messages: list[Any]) -> None:
        self.cost_usd = cost_usd
        self.turn_count = turn_count
        self.messages = messages


def test_aggregate_counts_and_prefers_stream_final_over_stale_result() -> None:
    # Mirrors the real AgentLoop completion path: the final turn's text is
    # emitted as the last ``assistant`` event but is NOT appended to the result
    # event's message list (which keeps only the stale pre-tool preamble plus a
    # trailing tool message). The adapter must take the answer from the stream,
    # not from the stale ``res.messages`` — otherwise the graded artifact is lost.
    events = [
        LoopEvent(type=LoopEventType.tool_use, data=None, turn=1),
        LoopEvent(type=LoopEventType.tool_use, data=None, turn=2),
        LoopEvent(type=LoopEventType.assistant, data="Let me write the solution.", turn=1),
        LoopEvent(type=LoopEventType.assistant, data="```python\nx = 42\n```", turn=2),
        LoopEvent(
            type=LoopEventType.result,
            data=_Result(
                0.05,
                3,
                [
                    Message.user("q"),
                    Message.assistant("Let me write the solution."),
                    Message.tool("call-1", "wrote file"),
                ],
            ),
            turn=3,
        ),
    ]
    res = asyncio.run(aggregate_events(_stream(events)))
    assert isinstance(res, AgentResult)
    assert res.tool_calls_total == 2
    assert res.steps == 3
    assert res.cost == 0.05
    # The stream's final assistant text wins over the stale result-message preamble.
    assert res.output == "```python\nx = 42\n```"
    assert res.success is True
    assert res.error is None


def test_aggregate_falls_back_to_assistant_event_without_result_messages() -> None:
    events = [
        LoopEvent(type=LoopEventType.assistant, data="only text", turn=1),
        LoopEvent(type=LoopEventType.result, data=_Result(0.0, 1, []), turn=1),
    ]
    res = asyncio.run(aggregate_events(_stream(events)))
    assert res.output == "only text"
    assert res.steps == 1


def test_aggregate_records_error() -> None:
    events = [LoopEvent(type=LoopEventType.error, data="kaboom", turn=1)]
    res = asyncio.run(aggregate_events(_stream(events)))
    assert res.success is False
    assert "kaboom" in (res.error or "")


def test_last_assistant_text_picks_last_assistant() -> None:
    msgs = [
        Message.assistant("first"),
        Message.user("mid"),
        Message.assistant("second"),
    ]
    assert _last_assistant_text(msgs) == "second"


def test_last_assistant_text_empty_when_none() -> None:
    assert _last_assistant_text([Message.user("only user")]) == ""
    assert _last_assistant_text(None) == ""


def test_adapter_isolates_failure(monkeypatch: Any) -> None:
    # If the agent blows up, the adapter must yield a failed result rather than
    # raise — one bad task cannot abort the whole sweep.
    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("agent exploded")

    monkeypatch.setattr("chimera.assembly.coding_agent.CodingAgent", _boom)

    class _Env:
        workdir = "/tmp/whatever"

    res = CodingAgentAdapter(provider=object()).run("do something", _Env())
    assert isinstance(res, AgentResult)
    assert res.success is False
    assert "exploded" in (res.error or "")
