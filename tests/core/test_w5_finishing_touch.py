"""Pin tests for the W5 finishing-touch fixes (see research/mink/SHIP-IT.md).

Two real bugs the W1-W4 swarm missed and W5 patched:

1. ``ReAct.async_iter_steps`` was not invoking ``handler.on_tool_start`` /
   ``handler.on_tool_end`` after each parallel tool batch, so the TUI
   ``▶`` collapsed-tool marker never rendered for ``chimera mink -p``
   (async path). Fix in ``chimera/core/loop.py`` lines 595-603.

2. ``async_execute_tool_calls_incremental`` was not invoking
   ``_apply_pre_tool_use_hook``, so PreToolUse hooks from
   ``.claude/settings.json`` were silently skipped on async paths.
   Fix in ``chimera/core/tool_executor.py`` lines 546-554.
"""
from __future__ import annotations

import asyncio
from typing import Any

from chimera.core.context import Context
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.core.tool import BaseTool
from chimera.hooks.emitter import HookEmitter
from chimera.hooks.executor import HookExecutor
from chimera.hooks.hook_types import FunctionHook, HookMatcher, HookOutput
from chimera.providers.base import Provider, Response
from chimera.streaming.base import StreamHandler
from chimera.types import Message, ToolCall, ToolResult


class _RecordingHandler(StreamHandler):
    def __init__(self) -> None:
        self.events: list[tuple[str, tuple[Any, ...]]] = []

    def on_text(self, text: str) -> None: self.events.append(("text", (text,)))
    def on_tool_start(self, n: str, c: str) -> None: self.events.append(("tool_start", (n, c)))
    def on_tool_end(self, c: str, o: str) -> None: self.events.append(("tool_end", (c, o)))
    def on_step_start(self, s: int) -> None: self.events.append(("step_start", (s,)))
    def on_step_end(self, s: int) -> None: self.events.append(("step_end", (s,)))
    def on_done(self) -> None: self.events.append(("done", ()))


class _ScriptedProvider(Provider):
    """Returns scripted Responses, then empty-tool-call responses to terminate."""

    def __init__(self, responses: list[Response]) -> None:
        self._responses = list(responses)
        self._idx = 0

    def _next(self) -> Response:
        if self._idx >= len(self._responses):
            return Response(content="(done)", tool_calls=[], usage={})
        resp = self._responses[self._idx]
        self._idx += 1
        return resp

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None, thinking=None):
        return self._next()

    async def async_complete(self, messages, tools=None, temperature=0.0, max_tokens=None, thinking=None):  # noqa: E501
        return self._next()

    context_window = 200_000  # type: ignore[assignment]
    supports_tool_use = True  # type: ignore[assignment]
    model_name = "mock"  # type: ignore[assignment]


class _BashStub(BaseTool):
    name = "bash"
    description = "Echo command back"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }

    def __init__(self, output: str = "hi\n", side_effect_box: list[str] | None = None) -> None:
        self._output = output
        # Shared list lets tests assert the tool was (or wasn't) actually invoked.
        self._side_effect = side_effect_box if side_effect_box is not None else []

    def execute(self, args, env=None):
        self._side_effect.append(args.get("command", ""))
        return ToolResult(output=self._output)


def _build_emitter(callback) -> HookEmitter:
    matcher = HookMatcher(hooks=[FunctionHook(callback=callback)])
    return HookEmitter(executor=HookExecutor(), matchers=[matcher])


def _run(tool_calls: list[ToolCall], tools: list[BaseTool], config: LoopConfig) -> Any:
    """Drive one async ReAct turn: scripted call, then a no-tool 'done' response."""
    provider = _ScriptedProvider([
        Response(content="calling", tool_calls=tool_calls, usage={}),
        Response(content="done", tool_calls=[], usage={}),
    ])
    loop = ReAct(max_steps=4, config=config)
    context = Context(system="test")
    context.add(Message.user("go"))
    return asyncio.run(loop.async_run(provider, tools, context, None))


def test_async_loop_invokes_handler_on_tool_start() -> None:
    """One async tool call -> exactly one on_tool_start + on_tool_end pair."""
    handler = _RecordingHandler()
    tc = ToolCall(id="tc-1", name="bash", arguments={"command": "echo hi"})
    _run([tc], [_BashStub("hi\n")], LoopConfig(handler=handler))

    starts = [e for e in handler.events if e[0] == "tool_start"]
    ends = [e for e in handler.events if e[0] == "tool_end"]
    assert len(starts) == 1, f"expected 1 on_tool_start, got {handler.events!r}"
    assert starts[0] == ("tool_start", ("bash", "tc-1"))
    assert len(ends) == 1
    assert ends[0][1][0] == "tc-1"
    assert "hi" in ends[0][1][1]


def test_async_loop_invokes_handler_on_tool_start_for_parallel_calls() -> None:
    """3 parallel tool calls -> 3 starts + 3 ends, in tool-call slot order."""
    handler = _RecordingHandler()
    parallel = [
        ToolCall(id=f"tc-{i}", name="bash", arguments={"command": f"echo {i}"})
        for i in range(3)
    ]
    _run(parallel, [_BashStub("ok\n")], LoopConfig(handler=handler))

    starts = [e for e in handler.events if e[0] == "tool_start"]
    ends = [e for e in handler.events if e[0] == "tool_end"]
    assert len(starts) == 3, f"expected 3 starts, got {starts!r}"
    assert len(ends) == 3, f"expected 3 ends, got {ends!r}"
    # Order must mirror tool_calls slot order (loop.py iterates with enumerate).
    assert [s[1][1] for s in starts] == ["tc-0", "tc-1", "tc-2"]
    assert [e[1][0] for e in ends] == ["tc-0", "tc-1", "tc-2"]


def test_pre_tool_use_hook_fires_in_async_path() -> None:
    """A PreToolUse FunctionHook fires before the tool runs on the async path."""
    # Shared timeline orders by insertion index rather than wall-clock time
    # so the assertion stays deterministic on a fast event loop.
    timeline: list[str] = []

    def hook_cb(messages, abort):
        timeline.append("hook")
        return HookOutput(continue_execution=True)

    bash = _BashStub("ok\n")
    original_execute = bash.execute

    def _record_then_execute(args, env=None):
        timeline.append("tool")
        return original_execute(args, env=env)

    bash.execute = _record_then_execute  # type: ignore[method-assign]

    tc = ToolCall(id="tc-1", name="bash", arguments={"command": "echo hi"})
    _run([tc], [bash], LoopConfig(hook_emitter=_build_emitter(hook_cb)))

    assert timeline.count("hook") == 1, f"hook fired {timeline.count('hook')}x: {timeline!r}"
    assert timeline.count("tool") == 1
    assert timeline.index("hook") < timeline.index("tool"), timeline


def test_pre_tool_use_hook_can_block_in_async_path() -> None:
    """Hook continue=False prevents the tool from running on the async path."""
    hook_calls: list[str] = []

    def deny_hook(messages, abort):
        hook_calls.append("deny")
        return HookOutput(continue_execution=False, stop_reason="policy")

    side_effects: list[str] = []
    bash = _BashStub("should-not-run\n", side_effect_box=side_effects)

    tc = ToolCall(id="tc-1", name="bash", arguments={"command": "rm -rf /"})
    result = _run([tc], [bash], LoopConfig(hook_emitter=_build_emitter(deny_hook)))

    assert hook_calls == ["deny"]
    # bash side-effect list MUST stay empty: the hook blocked dispatch.
    assert side_effects == [], f"tool ran despite hook denial: {side_effects!r}"
    # Loop must terminate cleanly (not raise PermissionDenied on async path).
    assert result is not None
