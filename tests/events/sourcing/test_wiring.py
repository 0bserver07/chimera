"""Tests for additive emissions in tool_executor that feed the event-sourcing sink.

These tests exercise the *real* tool_executor entry point with a stub
provider/tool to verify:

* :class:`PermissionEvent` carries a ``call_id``.
* A tool failure causes an :class:`ErrorEvent` to be published in
  addition to the existing :class:`ToolResultEvent`.

The end-to-end EventSourcingSink path is covered separately in
``test_sink.py``; this file is a contract test for the wiring change.
"""
from __future__ import annotations

from chimera.core.context import Context
from chimera.core.loop_config import LoopConfig
from chimera.core.tool import BaseTool
from chimera.core.tool_executor import execute_tool_calls
from chimera.events.base import EventBus
from chimera.events.types import ErrorEvent, PermissionEvent
from chimera.permissions.base import (
    PermissionAction,
    PermissionPolicy,
)
from chimera.types import ToolCall, ToolResult


class _FailingTool(BaseTool):
    name = "fail"
    description = "always fails"
    parameters = {"type": "object", "properties": {}}

    def execute(self, args: dict, env: object | None = None) -> ToolResult:  # type: ignore[override]
        return ToolResult(output="", error="forced failure")


class _OkTool(BaseTool):
    name = "ok"
    description = "always succeeds"
    parameters = {"type": "object", "properties": {}}

    def execute(self, args: dict, env: object | None = None) -> ToolResult:  # type: ignore[override]
        return ToolResult(output="ok")


class _AskAll(PermissionPolicy):
    """Permission policy that returns ALLOW for every tool — used to exercise
    the PermissionEvent emission without prompting."""

    def evaluate(self, tool_name: str, args: dict) -> PermissionAction:  # type: ignore[override]
        return PermissionAction.ALLOW


def test_permission_event_carries_call_id() -> None:
    bus = EventBus()
    received: list[PermissionEvent] = []
    bus.subscribe("permission", lambda e: received.append(e))  # type: ignore[arg-type]

    cfg = LoopConfig(yolo_mode=False, permissions=_AskAll(), event_bus=bus)
    tool = _OkTool()
    ctx = Context()
    execute_tool_calls(
        [ToolCall(id="call-42", name="ok", arguments={})],
        {"ok": tool},
        ctx,
        env=None,
        config=cfg,
    )
    assert received, "PermissionEvent was not emitted"
    assert received[0].call_id == "call-42"
    assert received[0].action == "allow"


def test_failing_tool_emits_error_event() -> None:
    bus = EventBus()
    errors: list[ErrorEvent] = []
    bus.subscribe("error", lambda e: errors.append(e))  # type: ignore[arg-type]

    cfg = LoopConfig(yolo_mode=True, event_bus=bus)
    tool = _FailingTool()
    ctx = Context()
    execute_tool_calls(
        [ToolCall(id="call-1", name="fail", arguments={})],
        {"fail": tool},
        ctx,
        env=None,
        config=cfg,
    )
    assert errors, "ErrorEvent was not emitted on tool failure"
    assert "forced failure" in errors[0].error


def test_successful_tool_does_not_emit_error_event() -> None:
    bus = EventBus()
    errors: list[ErrorEvent] = []
    bus.subscribe("error", lambda e: errors.append(e))  # type: ignore[arg-type]

    cfg = LoopConfig(yolo_mode=True, event_bus=bus)
    tool = _OkTool()
    ctx = Context()
    execute_tool_calls(
        [ToolCall(id="call-1", name="ok", arguments={})],
        {"ok": tool},
        ctx,
        env=None,
        config=cfg,
    )
    assert not errors


