"""Tests for LoopConfig integration with all loop variants."""
from __future__ import annotations

import pytest

from chimera.core.context import Context
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.core.loops.plan_execute import PlanAndExecute
from chimera.core.loops.reflexion import Reflexion
from chimera.core.loops.tree_of_thought import TreeOfThought
from chimera.core.tool import BaseTool
from chimera.core.tool_executor import LoopBreak, PermissionAsk, execute_tool_calls
from chimera.detection.actions import LoopDetector, OnDetect
from chimera.events.base import EventBus
from chimera.events.types import (
    PermissionEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from chimera.permissions.base import PermissionAction, PermissionPolicy
from chimera.permissions.presets import AlwaysDeny, AutoApprove
from chimera.permissions.rule import PermissionRuleset, Rule
from chimera.types import Message, ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeProvider:
    """Provider that returns scripted responses."""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.model_name = "fake-model"

    def complete(self, messages, tools=None, **kwargs):
        resp = self._responses[self._idx % len(self._responses)]
        self._idx += 1
        return _FakeResponse(**resp)


class _FakeResponse:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.has_tool_calls = bool(self.tool_calls)
        self.usage = {"input_tokens": 10, "output_tokens": 5}


class _EchoTool(BaseTool):
    name = "echo"
    description = "Echo tool"

    def to_anthropic_schema(self):
        return {"name": "echo", "description": "Echo", "input_schema": {"type": "object"}}

    def execute(self, args, env=None):
        return ToolResult(output=str(args))


class _FailTool(BaseTool):
    name = "fail"
    description = "Fail tool"

    def to_anthropic_schema(self):
        return {"name": "fail", "description": "Fail", "input_schema": {"type": "object"}}

    def execute(self, args, env=None):
        return ToolResult(output="", error="Intentional failure")


# ---------------------------------------------------------------------------
# LoopConfig unit tests
# ---------------------------------------------------------------------------


class TestLoopConfigDefaults:
    def test_all_none_by_default(self):
        # NB: the test suite's autouse fixture sets CHIMERA_UNSAFE=1,
        # so the safety defaults are *not* installed here.  The
        # pin-the-safety-behaviour tests live in
        # ``test_loop_config_safety.py``.
        config = LoopConfig()
        assert config.permissions is None
        assert config.detector is None
        assert config.compaction is None
        assert config.handler is None
        assert config.event_bus is None
        assert config.auto_compact_threshold == 0.8


class TestExecuteToolCalls:
    def test_basic_execution(self):
        ctx = Context(system="test")
        tc = ToolCall(id="tc1", name="echo", arguments={"msg": "hello"})
        tool_map = {"echo": _EchoTool()}
        count = execute_tool_calls([tc], tool_map, ctx, None, None)
        assert count == 1
        msgs = ctx.to_messages()
        assert any("hello" in str(m) for m in msgs)

    def test_unknown_tool(self):
        ctx = Context(system="test")
        ctx.add(Message.assistant("", tool_calls=[ToolCall(id="tc1", name="missing", arguments={})]))
        tc = ToolCall(id="tc1", name="missing", arguments={})
        tool_map = {"echo": _EchoTool()}
        count = execute_tool_calls([tc], tool_map, ctx, None, None)
        assert count == 1
        msgs = ctx.to_messages()
        assert any("unknown tool" in str(m).lower() for m in msgs)

    def test_permission_deny_skips(self):
        ctx = Context(system="test")
        ctx.add(Message.assistant("", tool_calls=[ToolCall(id="tc1", name="echo", arguments={})]))
        tc = ToolCall(id="tc1", name="echo", arguments={})
        config = LoopConfig(permissions=AlwaysDeny())
        count = execute_tool_calls([tc], {"echo": _EchoTool()}, ctx, None, config)
        assert count == 1
        msgs = ctx.to_messages()
        assert any("Permission denied" in str(m) for m in msgs)

    def test_permission_ask_raises(self):
        class AskPolicy(PermissionPolicy):
            def evaluate(self, tool_name, arguments=None):
                return PermissionAction.ASK

        ctx = Context(system="test")
        tc = ToolCall(id="tc1", name="echo", arguments={})
        config = LoopConfig(permissions=AskPolicy())
        with pytest.raises(PermissionAsk):
            execute_tool_calls([tc], {"echo": _EchoTool()}, ctx, None, config)

    def test_permission_allow(self):
        ctx = Context(system="test")
        tc = ToolCall(id="tc1", name="echo", arguments={"x": 1})
        config = LoopConfig(permissions=AutoApprove())
        count = execute_tool_calls([tc], {"echo": _EchoTool()}, ctx, None, config)
        assert count == 1

    def test_event_bus_emits_tool_events(self):
        bus = EventBus()
        events = []
        bus.subscribe("tool_call", lambda e: events.append(e))
        bus.subscribe("tool_result", lambda e: events.append(e))

        ctx = Context(system="test")
        tc = ToolCall(id="tc1", name="echo", arguments={"msg": "hi"})
        config = LoopConfig(event_bus=bus)
        execute_tool_calls([tc], {"echo": _EchoTool()}, ctx, None, config)

        assert len(events) == 2
        assert isinstance(events[0], ToolCallEvent)
        assert events[0].tool_name == "echo"
        assert isinstance(events[1], ToolResultEvent)
        assert events[1].success is True

    def test_event_bus_permission_event(self):
        bus = EventBus()
        events = []
        bus.subscribe("permission", lambda e: events.append(e))

        ctx = Context(system="test")
        ctx.add(Message.assistant("", tool_calls=[ToolCall(id="tc1", name="echo", arguments={})]))
        tc = ToolCall(id="tc1", name="echo", arguments={})
        config = LoopConfig(permissions=AutoApprove(), event_bus=bus)
        execute_tool_calls([tc], {"echo": _EchoTool()}, ctx, None, config)

        assert len(events) == 1
        assert isinstance(events[0], PermissionEvent)
        assert events[0].granted is True

    def test_loop_detection_break(self):
        detector = LoopDetector(on_detect=OnDetect.BREAK, threshold=2)
        ctx = Context(system="test")
        config = LoopConfig(detector=detector)

        tc = ToolCall(id="tc1", name="echo", arguments={"x": 1})
        # First call — no detection
        execute_tool_calls([tc], {"echo": _EchoTool()}, ctx, None, config)
        # Second call — should trigger
        with pytest.raises(LoopBreak):
            tc2 = ToolCall(id="tc2", name="echo", arguments={"x": 1})
            execute_tool_calls([tc2], {"echo": _EchoTool()}, ctx, None, config)

    def test_loop_detection_ask(self):
        detector = LoopDetector(on_detect=OnDetect.ASK, threshold=2)
        ctx = Context(system="test")
        config = LoopConfig(detector=detector)

        tc = ToolCall(id="tc1", name="echo", arguments={"x": 1})
        execute_tool_calls([tc], {"echo": _EchoTool()}, ctx, None, config)

        with pytest.raises(PermissionAsk):
            tc2 = ToolCall(id="tc2", name="echo", arguments={"x": 1})
            execute_tool_calls([tc2], {"echo": _EchoTool()}, ctx, None, config)


# ---------------------------------------------------------------------------
# ReAct loop integration
# ---------------------------------------------------------------------------


class TestReActWithConfig:
    def test_no_config_works_as_before(self):
        provider = _FakeProvider([{"content": "Hello, done!"}])
        loop = ReAct(max_steps=5)
        ctx = Context(system="test")
        result = loop.run(provider, [], ctx, None)
        assert result.success is True
        assert result.output == "Hello, done!"

    def test_with_event_bus(self):
        bus = EventBus()
        step_events = []
        bus.subscribe("step", lambda e: step_events.append(e))

        provider = _FakeProvider([
            {"content": "", "tool_calls": [ToolCall(id="tc1", name="echo", arguments={"x": 1})]},
            {"content": "Done"},
        ])
        config = LoopConfig(event_bus=bus)
        loop = ReAct(max_steps=5, config=config)
        ctx = Context(system="test")
        result = loop.run(provider, [_EchoTool()], ctx, None)

        assert result.success is True
        assert len(step_events) == 2
        assert step_events[0].step_number == 1
        assert step_events[1].step_number == 2

    def test_loop_break_returns_failure(self):
        detector = LoopDetector(on_detect=OnDetect.BREAK, threshold=2)
        config = LoopConfig(detector=detector)

        provider = _FakeProvider([
            {"content": "", "tool_calls": [ToolCall(id="tc1", name="echo", arguments={"x": 1})]},
        ])
        loop = ReAct(max_steps=10, config=config)
        ctx = Context(system="test")
        result = loop.run(provider, [_EchoTool()], ctx, None)

        assert result.success is False
        assert "Loop detected" in result.error

    def test_permission_deny_continues(self):
        config = LoopConfig(permissions=AlwaysDeny())
        provider = _FakeProvider([
            {"content": "", "tool_calls": [ToolCall(id="tc1", name="echo", arguments={})]},
            {"content": "Denied, so here's text"},
        ])
        loop = ReAct(max_steps=5, config=config)
        ctx = Context(system="test")
        result = loop.run(provider, [_EchoTool()], ctx, None)
        assert result.success is True

    def test_permission_rules(self):
        rules = PermissionRuleset(rules=[
            Rule("echo", action=PermissionAction.ALLOW),
            Rule("fail", action=PermissionAction.DENY),
        ])
        bus = EventBus()
        perm_events = []
        bus.subscribe("permission", lambda e: perm_events.append(e))

        config = LoopConfig(permissions=rules, event_bus=bus)
        provider = _FakeProvider([
            {"content": "", "tool_calls": [
                ToolCall(id="tc1", name="echo", arguments={"x": 1}),
                ToolCall(id="tc2", name="fail", arguments={}),
            ]},
            {"content": "Done"},
        ])
        loop = ReAct(max_steps=5, config=config)
        ctx = Context(system="test")
        result = loop.run(provider, [_EchoTool(), _FailTool()], ctx, None)

        assert result.success is True
        assert len(perm_events) == 2
        assert perm_events[0].granted is True  # echo allowed
        assert perm_events[1].granted is False  # fail denied


# ---------------------------------------------------------------------------
# PlanAndExecute integration
# ---------------------------------------------------------------------------


class TestPlanAndExecuteWithConfig:
    def test_no_config_works_as_before(self):
        provider = _FakeProvider([
            {"content": "Here's my plan..."},
            {"content": "Done executing"},
        ])
        loop = PlanAndExecute(max_steps=5)
        ctx = Context(system="test")
        result = loop.run(provider, [_EchoTool()], ctx, None)
        assert result.success is True

    def test_with_event_bus(self):
        bus = EventBus()
        step_events = []
        bus.subscribe("step", lambda e: step_events.append(e))

        provider = _FakeProvider([
            {"content": "Plan: do X then Y"},
            {"content": "", "tool_calls": [ToolCall(id="tc1", name="echo", arguments={"x": 1})]},
            {"content": "All done"},
        ])
        config = LoopConfig(event_bus=bus)
        loop = PlanAndExecute(max_steps=10, config=config)
        ctx = Context(system="test")
        result = loop.run(provider, [_EchoTool()], ctx, None)

        assert result.success is True
        assert len(step_events) == 3  # plan step + execute step + final step

    def test_loop_break(self):
        detector = LoopDetector(on_detect=OnDetect.BREAK, threshold=2)
        config = LoopConfig(detector=detector)

        # PlanAndExecute: plan first, then repeated tool calls to trigger detection
        provider = _FakeProvider([
            {"content": "Plan: do X"},
            # After plan + EXECUTE_PROMPT, keep returning same tool call
            {"content": "", "tool_calls": [ToolCall(id="tc1", name="echo", arguments={"x": 1})]},
            {"content": "", "tool_calls": [ToolCall(id="tc2", name="echo", arguments={"x": 1})]},
        ])
        loop = PlanAndExecute(max_steps=10, config=config)
        ctx = Context(system="test")
        result = loop.run(provider, [_EchoTool()], ctx, None)

        assert result.success is False
        assert "Loop detected" in result.error


# ---------------------------------------------------------------------------
# Reflexion integration
# ---------------------------------------------------------------------------


class TestReflexionWithConfig:
    def test_no_config_works_as_before(self):
        provider = _FakeProvider([{"content": "Reflected and done"}])
        loop = Reflexion(max_steps=5, reflect_every=2)
        ctx = Context(system="test")
        result = loop.run(provider, [], ctx, None)
        assert result.success is True

    def test_with_event_bus(self):
        bus = EventBus()
        step_events = []
        bus.subscribe("step", lambda e: step_events.append(e))

        provider = _FakeProvider([
            {"content": "", "tool_calls": [ToolCall(id="tc1", name="echo", arguments={"x": 1})]},
            {"content": "Done reflecting"},
        ])
        config = LoopConfig(event_bus=bus)
        loop = Reflexion(max_steps=5, reflect_every=3, config=config)
        ctx = Context(system="test")
        result = loop.run(provider, [_EchoTool()], ctx, None)

        assert result.success is True
        assert len(step_events) == 2

    def test_loop_break(self):
        detector = LoopDetector(on_detect=OnDetect.BREAK, threshold=2)
        config = LoopConfig(detector=detector)

        provider = _FakeProvider([
            {"content": "", "tool_calls": [ToolCall(id="tc1", name="echo", arguments={"x": 1})]},
        ])
        loop = Reflexion(max_steps=10, reflect_every=2, config=config)
        ctx = Context(system="test")
        result = loop.run(provider, [_EchoTool()], ctx, None)

        assert result.success is False
        assert "Loop detected" in result.error


# ---------------------------------------------------------------------------
# TreeOfThought integration
# ---------------------------------------------------------------------------


class TestTreeOfThoughtWithConfig:
    def test_no_config_works_as_before(self):
        provider = _FakeProvider([{"content": "Best answer"}])
        loop = TreeOfThought(max_steps=5, n_candidates=2)
        ctx = Context(system="test")
        result = loop.run(provider, [], ctx, None)
        assert result.success is True

    def test_with_event_bus_tool_calls(self):
        bus = EventBus()
        tool_events = []
        bus.subscribe("tool_call", lambda e: tool_events.append(e))

        # First candidate has tool calls, second doesn't
        responses = [
            {"content": "Using tool", "tool_calls": [ToolCall(id="tc1", name="echo", arguments={"x": 1})]},
            {"content": "No tools"},
            {"content": "Final answer"},
        ]
        provider = _FakeProvider(responses)
        config = LoopConfig(event_bus=bus)
        loop = TreeOfThought(max_steps=5, n_candidates=2, config=config)
        ctx = Context(system="test")
        result = loop.run(provider, [_EchoTool()], ctx, None)

        assert result.success is True
        # Both candidates produce a tool call event (same ToolCall id reused by fake provider)
        assert len(tool_events) >= 1
        assert tool_events[0].tool_name == "echo"


# ---------------------------------------------------------------------------
# Top-level import tests
# ---------------------------------------------------------------------------


class TestTopLevelImports:
    def test_loop_config_importable(self):
        from chimera import LoopConfig
        config = LoopConfig()
        assert config.permissions is None

    def test_all_new_modules_importable(self):
        from chimera import (
            EventBus,
            AgentRegistry,
        )
        # Smoke test — just verify they're importable
        assert EventBus is not None
        assert AgentRegistry is not None
