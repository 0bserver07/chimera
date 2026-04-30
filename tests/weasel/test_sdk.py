"""Tests for :mod:`chimera.weasel.sdk` — the embeddable Agent surface.

These tests use a tiny in-process :class:`MockProvider` so they exercise
all five SDK entrypoints (``run`` / ``arun`` / ``stream`` / ``astream`` /
``chat``) without touching the network. Each test asserts the **return
shape** the public API promises so the SDK contract is locked in.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

from chimera.core.tool import BaseTool
from chimera.providers.base import Provider, Response, StreamEvent
from chimera.types import AgentResult, Message, ToolCall, ToolResult
from chimera.weasel.sdk import Agent, Event, EventType


# ---------------------------------------------------------------------------
# Mock provider + tool
# ---------------------------------------------------------------------------


class MockProvider(Provider):
    """Provider that returns a predetermined sequence of responses.

    Mirrors the shape used in ``tests/core/test_agent.py`` so the SDK
    suite stays consistent with the rest of the codebase.
    """

    def __init__(self, responses: list[Response]) -> None:
        self._responses = list(responses)
        self._call_count = 0

    def _next(self) -> Response:
        if self._call_count >= len(self._responses):
            return Response(
                content="(no more responses)", tool_calls=[], usage={},
            )
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp

    def complete(  # type: ignore[override]
        self,
        messages: list[Message],
        tools: list[Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Response:
        return self._next()

    async def async_complete(  # type: ignore[override]
        self,
        messages: list[Message],
        tools: list[Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Response:
        return self._next()

    def stream(  # type: ignore[override]
        self,
        messages: list[Message],
        tools: list[Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[StreamEvent]:
        resp = self._next()
        if resp.content:
            yield StreamEvent(type="text_delta", content=resp.content)
        for tc in resp.tool_calls:
            yield StreamEvent(type="tool_call_complete", tool_call=tc)
        yield StreamEvent(type="done", usage=resp.usage)

    async def async_stream(  # type: ignore[override]
        self,
        messages: list[Message],
        tools: list[Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any | None = None,
        cancel_event: threading.Event | None = None,
    ) -> AsyncIterator[StreamEvent]:
        for ev in self.stream(messages, tools, temperature, max_tokens, thinking, cancel_event):
            yield ev

    @property
    def context_window(self) -> int:
        return 200_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "mock-weasel"


class EchoTool(BaseTool):
    """Trivial tool that echoes its input — used to exercise tool routing."""

    name = "echo"
    description = "Echo a message"
    parameters = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }

    def execute(self, args: dict[str, Any], env: Any) -> ToolResult:
        return ToolResult(output=f"Echo: {args['message']}")


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_agent_constructs_with_explicit_provider() -> None:
    """Passing ``provider=`` skips the default-resolution chain."""
    provider = MockProvider([Response(content="hi", tool_calls=[], usage={})])
    agent = Agent(provider=provider, tools=[])
    assert agent.provider is provider
    assert agent.tools == []
    assert agent.model_name == "mock-weasel"


def test_agent_uses_explicit_tools() -> None:
    """The supplied tool list is what the underlying core sees."""
    provider = MockProvider([Response(content="ok", tool_calls=[], usage={})])
    tool = EchoTool()
    agent = Agent(provider=provider, tools=[tool])
    assert agent.tools == [tool]


def test_agent_default_tools_when_none() -> None:
    """When ``tools=None`` we fall back to weasel's AGENT_TOOLS group."""
    provider = MockProvider([Response(content="ok", tool_calls=[], usage={})])
    agent = Agent(provider=provider)
    # AGENT_TOOLS is non-empty; we don't pin the exact size because it
    # evolves, only that the SDK populated *something* sensible.
    assert len(agent.tools) > 0


# ---------------------------------------------------------------------------
# run() — sync one-shot
# ---------------------------------------------------------------------------


def test_run_returns_agent_result() -> None:
    """``run`` returns an :class:`AgentResult` with a populated output."""
    provider = MockProvider(
        [Response(content="Hello, world.", tool_calls=[], usage={})],
    )
    agent = Agent(provider=provider, tools=[])
    result = agent.run("Say hi")
    assert isinstance(result, AgentResult)
    assert result.success is True
    assert "Hello" in result.output
    assert result.steps >= 1


def test_run_with_tool_call() -> None:
    """A tool round-trip flows through the SDK without surprises."""
    provider = MockProvider([
        Response(
            content="Calling echo.",
            tool_calls=[ToolCall(id="t1", name="echo", arguments={"message": "hi"})],
            usage={},
        ),
        Response(content="Done.", tool_calls=[], usage={}),
    ])
    agent = Agent(provider=provider, tools=[EchoTool()])
    result = agent.run("Echo hi")
    assert result.success is True
    assert result.tool_calls_total == 1


# ---------------------------------------------------------------------------
# arun() — async one-shot
# ---------------------------------------------------------------------------


def test_arun_returns_agent_result() -> None:
    """``arun`` is awaitable and returns the same shape as ``run``."""
    provider = MockProvider(
        [Response(content="async hi", tool_calls=[], usage={})],
    )
    agent = Agent(provider=provider, tools=[])
    result = asyncio.run(agent.arun("greet"))
    assert isinstance(result, AgentResult)
    assert result.success is True
    assert "async hi" in result.output


# ---------------------------------------------------------------------------
# stream() — sync iterator
# ---------------------------------------------------------------------------


def test_stream_yields_text_step_done_in_order() -> None:
    """A text-only response yields ``text`` → ``step`` → ``done``."""
    provider = MockProvider(
        [Response(content="streamed reply", tool_calls=[], usage={})],
    )
    agent = Agent(provider=provider, tools=[])
    events = list(agent.stream("hi"))

    types = [e.type for e in events]
    assert EventType.DONE in types
    assert types[-1] == EventType.DONE  # done is always last
    assert types.index(EventType.TEXT) < types.index(EventType.STEP)
    assert types.index(EventType.STEP) < types.index(EventType.DONE)

    text_events = [e for e in events if e.type == EventType.TEXT]
    assert any("streamed reply" in e.text for e in text_events)

    done = events[-1]
    assert isinstance(done.result, AgentResult)
    assert done.result.success is True


def test_stream_emits_tool_call_and_tool_result() -> None:
    """A tool round-trip surfaces ``tool_call`` + ``tool_result`` events."""
    provider = MockProvider([
        Response(
            content="",
            tool_calls=[ToolCall(id="t1", name="echo", arguments={"message": "x"})],
            usage={},
        ),
        Response(content="finished", tool_calls=[], usage={}),
    ])
    agent = Agent(provider=provider, tools=[EchoTool()])
    events = list(agent.stream("call echo"))

    tool_call_events = [e for e in events if e.type == EventType.TOOL_CALL]
    tool_result_events = [e for e in events if e.type == EventType.TOOL_RESULT]
    assert len(tool_call_events) == 1
    assert tool_call_events[0].tool_call is not None
    assert tool_call_events[0].tool_call.name == "echo"
    assert len(tool_result_events) == 1
    assert tool_result_events[0].tool_result is not None
    assert "Echo: x" in tool_result_events[0].tool_result.output


# ---------------------------------------------------------------------------
# astream() — async iterator
# ---------------------------------------------------------------------------


def test_astream_yields_done_with_result() -> None:
    """``astream`` mirrors ``stream`` and terminates with ``done``."""
    provider = MockProvider(
        [Response(content="async stream", tool_calls=[], usage={})],
    )
    agent = Agent(provider=provider, tools=[])

    async def collect() -> list[Event]:
        out: list[Event] = []
        async for ev in agent.astream("hi"):
            out.append(ev)
        return out

    events = asyncio.run(collect())
    assert events[-1].type == EventType.DONE
    assert isinstance(events[-1].result, AgentResult)
    assert events[-1].result.success is True
    assert any(e.type == EventType.TEXT and "async stream" in e.text for e in events)


# ---------------------------------------------------------------------------
# chat() — multi-turn
# ---------------------------------------------------------------------------


def test_chat_returns_assistant_text() -> None:
    """``chat`` returns the assistant's text output as a string."""
    provider = MockProvider(
        [Response(content="first reply", tool_calls=[], usage={})],
    )
    agent = Agent(provider=provider, tools=[])
    out = agent.chat("hello")
    assert isinstance(out, str)
    assert "first reply" in out


def test_chat_preserves_history_across_turns() -> None:
    """A second ``chat`` turn sees the first turn's messages in context."""
    provider = MockProvider([
        Response(content="reply 1", tool_calls=[], usage={}),
        Response(content="reply 2", tool_calls=[], usage={}),
    ])
    agent = Agent(provider=provider, tools=[])
    agent.chat("turn 1")
    agent.chat("turn 2")

    # The internal Session should hold both user turns + both assistant
    # turns. We poke at the private attribute deliberately to lock the
    # contract: ``chat`` is *stateful*.
    session = agent._session
    assert session is not None
    msgs = session.messages
    user_msgs = [m for m in msgs if m.role == "user"]
    assert len(user_msgs) == 2
    assert user_msgs[0].content == "turn 1"
    assert user_msgs[1].content == "turn 2"


def test_reset_chat_drops_session() -> None:
    """``reset_chat`` clears the cached :class:`Session`."""
    provider = MockProvider(
        [Response(content="x", tool_calls=[], usage={}),
         Response(content="y", tool_calls=[], usage={})],
    )
    agent = Agent(provider=provider, tools=[])
    agent.chat("turn")
    assert agent._session is not None
    agent.reset_chat()
    assert agent._session is None


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_event_type_constants_are_strings() -> None:
    """The :class:`EventType` constants are bare strings — easy to compare."""
    assert EventType.TEXT == "text"
    assert EventType.TOOL_CALL == "tool_call"
    assert EventType.TOOL_RESULT == "tool_result"
    assert EventType.STEP == "step"
    assert EventType.DONE == "done"


def test_event_dataclass_defaults() -> None:
    """:class:`Event` carries sensible defaults for unset fields."""
    ev = Event(type=EventType.TEXT, text="hi")
    assert ev.type == "text"
    assert ev.text == "hi"
    assert ev.tool_call is None
    assert ev.tool_result is None
    assert ev.result is None
    assert ev.data == {}


def test_sdk_exports() -> None:
    """The module surface stays minimal and explicit."""
    import chimera.weasel.sdk as sdk

    assert "Agent" in sdk.__all__
    assert "Event" in sdk.__all__
    assert "EventType" in sdk.__all__
    assert "AgentResult" in sdk.__all__


# ---------------------------------------------------------------------------
# Default-provider plumbing
# ---------------------------------------------------------------------------


def test_default_provider_is_consulted_when_no_provider_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``provider=None``, the SDK calls
    :func:`chimera.providers.factory.create_provider`.
    """
    sentinel = MockProvider([Response(content="ok", tool_calls=[], usage={})])
    seen: dict[str, Any] = {}

    def fake_create_provider(model: str | None = None, **kwargs: Any) -> Provider:
        seen["model"] = model
        return sentinel

    import chimera.providers.factory as factory_mod

    monkeypatch.setattr(factory_mod, "create_provider", fake_create_provider)
    agent = Agent(model="glm-5", tools=[])
    assert agent.provider is sentinel
    assert seen["model"] == "glm-5"
