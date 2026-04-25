"""Tests for ToolResultEvent.tool_metadata forwarding."""
from __future__ import annotations

from typing import Any


from chimera.core.context import Context
from chimera.core.tool import BaseTool
from chimera.core.tool_executor import execute_tool_calls, execute_tool_calls_incremental
from chimera.env.base import Environment
from chimera.events.base import EventBus
from chimera.events.types import ToolResultEvent
from chimera.types import Message, ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MetadataTool(BaseTool):
    name = "meta_tool"
    description = "Tool that returns metadata"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        return ToolResult(
            output="done",
            metadata={"file_change": {"path": "/foo.py", "type": "create"}},
        )


class PlainTool(BaseTool):
    name = "plain_tool"
    description = "Tool with no metadata"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        return ToolResult(output="ok")


# ---------------------------------------------------------------------------
# Tests: ToolResultEvent defaults
# ---------------------------------------------------------------------------

class TestToolResultEventDefaults:
    def test_default_empty_metadata(self) -> None:
        event = ToolResultEvent(call_id="c1", output="done", success=True)
        assert event.tool_metadata == {}

    def test_backward_compat_no_kwarg(self) -> None:
        event = ToolResultEvent()
        assert event.call_id == ""
        assert event.tool_metadata == {}


# ---------------------------------------------------------------------------
# Tests: execute_tool_calls forwards metadata
# ---------------------------------------------------------------------------

class TestExecuteToolCallsMetadata:
    def test_forwards_metadata_via_event_bus(self) -> None:
        from chimera.core.loop_config import LoopConfig

        bus = EventBus()
        received: list[ToolResultEvent] = []
        bus.subscribe("tool_result", lambda e: received.append(e))
        config = LoopConfig(event_bus=bus)

        tc = ToolCall(id="c1", name="meta_tool", arguments={})
        tool_map = {"meta_tool": MetadataTool()}
        context = Context(system="test")
        context.add(Message.user("go"))

        execute_tool_calls([tc], tool_map, context, None, config)

        assert len(received) == 1
        assert received[0].tool_metadata == {
            "file_change": {"path": "/foo.py", "type": "create"},
        }

    def test_empty_metadata_forwarded(self) -> None:
        from chimera.core.loop_config import LoopConfig

        bus = EventBus()
        received: list[ToolResultEvent] = []
        bus.subscribe("tool_result", lambda e: received.append(e))
        config = LoopConfig(event_bus=bus)

        tc = ToolCall(id="c1", name="plain_tool", arguments={})
        tool_map = {"plain_tool": PlainTool()}
        context = Context(system="test")
        context.add(Message.user("go"))

        execute_tool_calls([tc], tool_map, context, None, config)

        assert len(received) == 1
        assert received[0].tool_metadata == {}


# ---------------------------------------------------------------------------
# Tests: execute_tool_calls_incremental forwards metadata
# ---------------------------------------------------------------------------

class TestIncrementalMetadata:
    def test_forwards_metadata_via_event_bus(self) -> None:
        from chimera.core.loop_config import LoopConfig

        bus = EventBus()
        received: list[ToolResultEvent] = []
        bus.subscribe("tool_result", lambda e: received.append(e))
        config = LoopConfig(event_bus=bus)

        tc = ToolCall(id="c1", name="meta_tool", arguments={})
        tool_map = {"meta_tool": MetadataTool()}
        context = Context(system="test")
        context.add(Message.user("go"))

        result = execute_tool_calls_incremental([tc], tool_map, context, None, config)

        assert result.executed == 1
        assert len(received) == 1
        assert received[0].tool_metadata == {
            "file_change": {"path": "/foo.py", "type": "create"},
        }
