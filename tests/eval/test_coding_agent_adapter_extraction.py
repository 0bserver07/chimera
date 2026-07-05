"""Extraction tests for the CodingAgent -> Harness adapter (GAP B).

The adapter must return the agent's *true* final message — the graded artifact —
from a faked ``LoopEvent`` stream. These tests pin the specific failure that made
assembled-preset agents score 0% on answer-graded benchmarks: AgentLoop's
completion branch yields its terminal ``result`` event WITHOUT appending the
final assistant turn to ``res.messages``, so reading the answer from that message
list returns a stale pre-tool preamble. The fix takes the answer from the
stream's last ``assistant`` event instead. The stream is faked exactly the way
``test_coding_agent_adapter.py`` does (an async generator of ``LoopEvent`` plus a
stand-in ``result`` payload).
"""

from __future__ import annotations

import asyncio
from typing import Any

from chimera.core.loop_events import LoopEvent, LoopEventType
from chimera.eval.coding_agent_adapter import aggregate_events
from chimera.types import Message


async def _stream(events: list[LoopEvent]) -> Any:
    for event in events:
        yield event


class _Result:
    """Stand-in for the loop's terminal ``result`` event payload."""

    def __init__(self, cost_usd: float, turn_count: int, messages: list[Any]) -> None:
        self.cost_usd = cost_usd
        self.turn_count = turn_count
        self.messages = messages


def _run(events: list[LoopEvent]) -> Any:
    return asyncio.run(aggregate_events(_stream(events)))


def test_final_fenced_answer_survives_stale_result_messages() -> None:
    # The real completion shape: turn 1 is a pre-tool preamble (which is the only
    # assistant message the result event carries), turn 2 is the final fenced
    # answer (emitted on the stream, absent from res.messages). The graded output
    # must be the fenced code, not the preamble.
    final = "```python\ndef solution():\n    return 7\n```"
    events = [
        LoopEvent(type=LoopEventType.assistant, data="I'll implement it now.", turn=1),
        LoopEvent(type=LoopEventType.tool_use, data=None, turn=1),
        LoopEvent(type=LoopEventType.assistant, data=final, turn=2),
        LoopEvent(
            type=LoopEventType.result,
            data=_Result(
                0.02,
                2,
                [
                    Message.user("task"),
                    Message.assistant("I'll implement it now."),
                    Message.tool("c1", "wrote solution.py"),
                ],
            ),
            turn=2,
        ),
    ]
    res = _run(events)
    assert res.output == final
    assert res.success is True


def test_response_payload_content_is_extracted() -> None:
    # Assistant events carry a provider ``Response``-like object (``.content``),
    # not a bare string. The adapter's ``_text_of`` must read ``.content``.
    class _Response:
        def __init__(self, content: str) -> None:
            self.content = content
            self.tool_calls: list[Any] = []

    final = "```python\nANSWER = 1\n```"
    events = [
        LoopEvent(type=LoopEventType.assistant, data=_Response("preamble"), turn=1),
        LoopEvent(type=LoopEventType.assistant, data=_Response(final), turn=2),
        LoopEvent(type=LoopEventType.result, data=_Result(0.0, 2, []), turn=2),
    ]
    res = _run(events)
    assert res.output == final


def test_falls_back_to_result_messages_when_no_assistant_events() -> None:
    # A loop type that emits only a terminal result (no per-turn assistant
    # events) must still yield an answer — from the result's message list.
    events = [
        LoopEvent(
            type=LoopEventType.result,
            data=_Result(0.0, 1, [Message.user("q"), Message.assistant("the answer")]),
            turn=1,
        ),
    ]
    res = _run(events)
    assert res.output == "the answer"


def test_empty_when_no_text_anywhere() -> None:
    events = [
        LoopEvent(type=LoopEventType.tool_use, data=None, turn=1),
        LoopEvent(type=LoopEventType.result, data=_Result(0.0, 1, []), turn=1),
    ]
    res = _run(events)
    assert res.output == ""
    assert res.success is True
