"""W13-G4 — verify the loop & tool-executor lifecycle hooks emit.

Asserts that running ``ReAct`` (sync + async) and the shared tool
executor fire the canonical Claude-Code hook events at the expected
points:

* ``SESSION_START`` — first thing in :meth:`ReAct.iter_steps` /
  :meth:`ReAct.async_iter_steps`.
* ``USER_PROMPT_SUBMIT`` — when the conversation context already
  carries a user message at loop entry.
* ``PRE_TOOL_USE`` — before each tool dispatch (executor hook).
* ``POST_TOOL_USE`` — after each successful tool dispatch.
* ``POST_TOOL_USE_FAILURE`` — after each failed tool dispatch.
* ``STOP`` — when the loop exits cleanly with no further tool calls.
* ``NOTIFICATION`` — fired alongside ``STOP`` carrying the agent's
  final text.
* ``SESSION_END`` — last thing on every termination path.
* ``STOP_FAILURE`` — on any non-clean termination (cost limit, loop
  break, max steps, cancellation).

The recording emitter swallows every emission so dispatch semantics
(matchers, command/prompt/function shapes) stay isolated from this
wiring test — a sibling concern covered by
:mod:`tests.hooks.test_emitter` and :mod:`tests.hooks.test_executor`.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from chimera.core.context import Context
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.core.tool import BaseTool
from chimera.hooks.emitter import HookEmitter
from chimera.hooks.events import HookEvent
from chimera.hooks.executor import HookExecutor
from chimera.hooks.hook_types import HookOutput
from chimera.providers.base import Provider, Response
from chimera.providers.cost_tracker import CostLimitExceeded, CostTracker
from chimera.types import Message, ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Recording emitter
# ---------------------------------------------------------------------------


class _RecordingExecutor(HookExecutor):
    """Capture every ``execute()`` call into a shared list."""

    def __init__(self, sink: list[tuple[HookEvent, dict[str, Any]]]) -> None:
        super().__init__()
        self._sink = sink

    async def execute(self, event, input_data, matchers, abort_signal=None):  # type: ignore[override]
        self._sink.append(
            (
                event,
                {
                    "tool_name": input_data.tool_name,
                    "tool_input": input_data.tool_input,
                    "tool_output": input_data.tool_output,
                    "tool_error": input_data.tool_error,
                    "user_prompt": input_data.user_prompt,
                },
            )
        )
        return HookOutput()


def _make_recorder() -> tuple[HookEmitter, list[tuple[HookEvent, dict[str, Any]]]]:
    sink: list[tuple[HookEvent, dict[str, Any]]] = []
    emitter = HookEmitter(executor=_RecordingExecutor(sink))
    return emitter, sink


def _events_in(sink: list[tuple[HookEvent, dict[str, Any]]]) -> list[HookEvent]:
    return [e for e, _ in sink]


# ---------------------------------------------------------------------------
# Mock provider + tools
# ---------------------------------------------------------------------------


class _MockProvider(Provider):
    def __init__(self, responses: list[Response]) -> None:
        self._responses = list(responses)
        self._i = 0

    def complete(self, messages, tools=None, **kwargs) -> Response:  # type: ignore[override]
        del messages, tools, kwargs
        if self._i >= len(self._responses):
            return Response(content="(end)", tool_calls=[], usage={})
        r = self._responses[self._i]
        self._i += 1
        return r

    async def async_complete(self, messages, tools=None, **kwargs) -> Response:  # type: ignore[override]
        return self.complete(messages, tools, **kwargs)

    @property
    def context_window(self) -> int:
        return 200_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "mock"


class _EchoTool(BaseTool):
    name = "echo"
    description = "Echo a message"
    parameters = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }

    def execute(self, args, env):
        return ToolResult(output=f"Echo: {args['message']}")

    async def async_execute(self, args, env):
        return self.execute(args, env)


class _FailingTool(BaseTool):
    name = "boom"
    description = "Always fails"
    parameters = {"type": "object", "properties": {}, "required": []}

    def execute(self, args, env):
        return ToolResult(output="", error="kaboom")

    async def async_execute(self, args, env):
        return self.execute(args, env)


# ---------------------------------------------------------------------------
# Sync iter_steps
# ---------------------------------------------------------------------------


class TestSyncLifecycleHooks:
    def test_session_start_user_prompt_stop_notification_session_end(self) -> None:
        emitter, sink = _make_recorder()
        cfg = LoopConfig(hook_emitter=emitter)
        provider = _MockProvider([Response(content="hi", tool_calls=[], usage={})])
        ctx = Context(system="sys")
        ctx.add(Message.user("hello there"))

        ReAct(max_steps=5, config=cfg).run(provider, [], ctx, env=None)

        events = _events_in(sink)
        assert events.index(HookEvent.SESSION_START) < events.index(
            HookEvent.USER_PROMPT_SUBMIT,
        )
        assert HookEvent.STOP in events
        assert HookEvent.NOTIFICATION in events
        assert HookEvent.SESSION_END in events
        # SessionEnd is the last lifecycle hook
        assert events[-1] == HookEvent.SESSION_END

        # UserPromptSubmit carries the user message verbatim
        for evt, payload in sink:
            if evt == HookEvent.USER_PROMPT_SUBMIT:
                assert payload["user_prompt"] == "hello there"
                break
        else:  # pragma: no cover
            pytest.fail("USER_PROMPT_SUBMIT not recorded")

    def test_pre_and_post_tool_use_fire_for_success(self) -> None:
        emitter, sink = _make_recorder()
        cfg = LoopConfig(hook_emitter=emitter)
        provider = _MockProvider([
            Response(
                content="",
                tool_calls=[ToolCall(id="tc1", name="echo", arguments={"message": "x"})],
                usage={},
            ),
            Response(content="done", tool_calls=[], usage={}),
        ])
        ctx = Context(system="sys")
        ctx.add(Message.user("go"))
        ReAct(max_steps=5, config=cfg).run(provider, [_EchoTool()], ctx, env=None)

        events = _events_in(sink)
        assert HookEvent.PRE_TOOL_USE in events
        assert HookEvent.POST_TOOL_USE in events
        assert HookEvent.POST_TOOL_USE_FAILURE not in events

    def test_post_tool_use_failure_fires_for_errors(self) -> None:
        emitter, sink = _make_recorder()
        cfg = LoopConfig(hook_emitter=emitter)
        provider = _MockProvider([
            Response(
                content="",
                tool_calls=[ToolCall(id="tc1", name="boom", arguments={})],
                usage={},
            ),
            Response(content="ok done", tool_calls=[], usage={}),
        ])
        ctx = Context(system="sys")
        ctx.add(Message.user("go"))
        ReAct(max_steps=5, config=cfg).run(provider, [_FailingTool()], ctx, env=None)

        events = _events_in(sink)
        assert HookEvent.PRE_TOOL_USE in events
        assert HookEvent.POST_TOOL_USE_FAILURE in events
        assert HookEvent.POST_TOOL_USE not in events

    def test_stop_failure_on_max_steps(self) -> None:
        emitter, sink = _make_recorder()
        cfg = LoopConfig(hook_emitter=emitter)
        # Always returns a tool call, never a final answer → max_steps reached.
        provider = _MockProvider([
            Response(
                content="",
                tool_calls=[ToolCall(id=f"tc{i}", name="echo", arguments={"message": "x"})],
                usage={},
            )
            for i in range(5)
        ])
        ctx = Context(system="sys")
        ctx.add(Message.user("loop please"))
        ReAct(max_steps=2, config=cfg).run(provider, [_EchoTool()], ctx, env=None)

        events = _events_in(sink)
        assert HookEvent.STOP_FAILURE in events
        assert HookEvent.SESSION_END in events
        # STOP must NOT fire on a failure path
        assert HookEvent.STOP not in events

    def test_stop_failure_on_cost_limit(self) -> None:
        emitter, sink = _make_recorder()
        tracker = CostTracker(budget=0.0)
        cfg = LoopConfig(hook_emitter=emitter, cost_tracker=tracker)
        provider = _MockProvider([
            Response(content="hi", tool_calls=[], usage={"input_tokens": 1, "output_tokens": 1}),
        ])

        # Force record() to raise immediately so the cost-limit branch fires
        # regardless of the configured pricing. The lifecycle hook wiring is
        # what's under test, not CostTracker accounting.
        def _raise(*_a: Any, **_k: Any) -> None:
            raise CostLimitExceeded("forced")

        tracker.record = _raise  # type: ignore[method-assign]

        ctx = Context(system="sys")
        ctx.add(Message.user("go"))
        ReAct(max_steps=5, config=cfg).run(provider, [], ctx, env=None)

        events = _events_in(sink)
        assert HookEvent.STOP_FAILURE in events
        assert HookEvent.SESSION_END in events


# ---------------------------------------------------------------------------
# Async iter_steps
# ---------------------------------------------------------------------------


class TestAsyncLifecycleHooks:
    def test_async_session_lifecycle(self) -> None:
        emitter, sink = _make_recorder()
        cfg = LoopConfig(hook_emitter=emitter)
        provider = _MockProvider([Response(content="ok", tool_calls=[], usage={})])
        ctx = Context(system="sys")
        ctx.add(Message.user("howdy"))

        async def _run() -> None:
            await ReAct(max_steps=5, config=cfg).async_run(provider, [], ctx, env=None)

        asyncio.run(_run())

        events = _events_in(sink)
        assert HookEvent.SESSION_START in events
        assert HookEvent.USER_PROMPT_SUBMIT in events
        assert HookEvent.STOP in events
        assert HookEvent.NOTIFICATION in events
        assert events[-1] == HookEvent.SESSION_END

    def test_async_post_tool_use_failure(self) -> None:
        emitter, sink = _make_recorder()
        cfg = LoopConfig(hook_emitter=emitter)
        provider = _MockProvider([
            Response(
                content="",
                tool_calls=[ToolCall(id="tc1", name="boom", arguments={})],
                usage={},
            ),
            Response(content="done", tool_calls=[], usage={}),
        ])
        ctx = Context(system="sys")
        ctx.add(Message.user("go"))

        async def _run() -> None:
            await ReAct(max_steps=5, config=cfg).async_run(
                provider, [_FailingTool()], ctx, env=None,
            )

        asyncio.run(_run())

        events = _events_in(sink)
        assert HookEvent.PRE_TOOL_USE in events
        assert HookEvent.POST_TOOL_USE_FAILURE in events
        assert HookEvent.POST_TOOL_USE not in events

    def test_async_stop_failure_on_max_steps(self) -> None:
        emitter, sink = _make_recorder()
        cfg = LoopConfig(hook_emitter=emitter)
        provider = _MockProvider([
            Response(
                content="",
                tool_calls=[ToolCall(id=f"tc{i}", name="echo", arguments={"message": "x"})],
                usage={},
            )
            for i in range(5)
        ])
        ctx = Context(system="sys")
        ctx.add(Message.user("loop please"))

        async def _run() -> None:
            await ReAct(max_steps=2, config=cfg).async_run(
                provider, [_EchoTool()], ctx, env=None,
            )

        asyncio.run(_run())

        events = _events_in(sink)
        assert HookEvent.STOP_FAILURE in events
        assert HookEvent.SESSION_END in events
        assert HookEvent.STOP not in events
