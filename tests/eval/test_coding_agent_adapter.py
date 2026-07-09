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


def test_aggregate_recovers_cost_when_stream_raises_before_result() -> None:
    # The regression: an unrecoverable provider error mid-run raises straight
    # through AgentLoop, so it never emits the terminal `result` event that used
    # to be cost's only source — and the assembled-agent path reported $0.00 for
    # runs that had already made real, billable LLM calls. Cost must instead be
    # recovered from the per-turn `assistant` usage the loop *did* stream.
    from chimera.providers.base import Response
    from chimera.providers.cost import calculate_cost

    async def _raising_stream() -> Any:
        yield LoopEvent(
            type=LoopEventType.assistant,
            data=Response("let me look", [], {"input_tokens": 1000, "output_tokens": 100}),
            turn=1,
        )
        yield LoopEvent(type=LoopEventType.tool_use, data=None, turn=1)
        yield LoopEvent(
            type=LoopEventType.assistant,
            data=Response("almost there", [], {"input_tokens": 500, "output_tokens": 50}),
            turn=2,
        )
        raise RuntimeError("simulated provider error")

    res = asyncio.run(aggregate_events(_raising_stream(), model="glm-5.2"))
    expected = calculate_cost("glm-5.2", {"input_tokens": 1500, "output_tokens": 150})
    assert expected > 0.0  # guard: the model is priced, so this is a real check
    assert res.cost == expected  # summed turn-1 + turn-2 usage, not zero
    assert res.success is False
    assert "simulated provider error" in (res.error or "")
    assert res.tool_calls_total == 1
    assert res.steps == 2  # two assistant turns observed before the raise


def test_aggregate_prefers_result_cost_and_does_not_double_count() -> None:
    # When a terminal `result` event arrives it stays authoritative: its cost is
    # used verbatim and the summed per-turn usage is NOT added on top.
    from chimera.providers.base import Response

    async def _stream_with_result() -> Any:
        yield LoopEvent(
            type=LoopEventType.assistant,
            data=Response("x", [], {"input_tokens": 1000, "output_tokens": 100}),
            turn=1,
        )
        yield LoopEvent(type=LoopEventType.result, data=_Result(0.05, 1, []), turn=1)

    res = asyncio.run(aggregate_events(_stream_with_result(), model="glm-5.2"))
    assert res.cost == 0.05


def test_adapter_reports_cost_when_provider_errors_midrun(tmp_path: Any) -> None:
    # End-to-end faux-provider proof: the non-streaming `swebench` preset lets a
    # mid-run provider error propagate (no `result` event), so this drives the
    # exact production path — CodingAgent -> AgentLoop -> aggregate_events — and
    # confirms the turn-1 call's real cost survives to the returned AgentResult.
    from types import SimpleNamespace

    from chimera.providers.base import Provider, Response
    from chimera.types import ToolCall

    class _FaultyProvider(Provider):
        """Succeeds once (billable), then raises like a rate-limited API."""

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages: Any, tools: Any = None, **kwargs: Any) -> Response:
            self.calls += 1
            if self.calls == 1:
                return Response(
                    "looking into it",
                    [ToolCall(id="c1", name="nonexistent_tool", arguments={})],
                    {"input_tokens": 1000, "output_tokens": 100},
                )
            raise RuntimeError("simulated rate limit")

        @property
        def context_window(self) -> int:
            return 128_000

        @property
        def supports_tool_use(self) -> bool:
            return True

        @property
        def model_name(self) -> str:
            return "glm-5.2"

    adapter = CodingAgentAdapter(_FaultyProvider(), preset="swebench")
    res = adapter.run("solve: return 42", SimpleNamespace(workdir=str(tmp_path)))
    assert isinstance(res, AgentResult)
    assert res.success is False  # the run did error out
    assert res.cost > 0.0  # ...but the turn-1 call's real cost is still reported


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
