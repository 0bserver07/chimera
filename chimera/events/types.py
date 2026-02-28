# chimera/events/types.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chimera.events.base import Event

__all__ = [
    "ToolCallEvent",
    "ToolResultEvent",
    "StepEvent",
    "TextDeltaEvent",
    "ErrorEvent",
    "LoopDetectedEvent",
    "CompactionEvent",
    "PermissionEvent",
    "SessionEvent",
]


@dataclass
class ToolCallEvent(Event):
    """A tool has been invoked."""

    type: str = field(default="tool_call", init=False)
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""


@dataclass
class ToolResultEvent(Event):
    """A tool call has completed."""

    type: str = field(default="tool_result", init=False)
    call_id: str = ""
    output: str = ""
    success: bool = True
    tool_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepEvent(Event):
    """An agent step has been executed."""

    type: str = field(default="step", init=False)
    step_number: int = 0
    content: str = ""


@dataclass
class TextDeltaEvent(Event):
    """Streaming text content received."""

    type: str = field(default="text_delta", init=False)
    content: str = ""


@dataclass
class ErrorEvent(Event):
    """An error occurred during execution."""

    type: str = field(default="error", init=False)
    error: str = ""
    recoverable: bool = True


@dataclass
class LoopDetectedEvent(Event):
    """The loop detector identified a repeating pattern."""

    type: str = field(default="loop_detected", init=False)
    pattern: str = ""


@dataclass
class CompactionEvent(Event):
    """Context compaction was performed."""

    type: str = field(default="compaction", init=False)
    messages_before: int = 0
    messages_after: int = 0


@dataclass
class PermissionEvent(Event):
    """A permission decision was made for a tool action."""

    type: str = field(default="permission", init=False)
    tool_name: str = ""
    action: str = ""
    granted: bool = False


@dataclass
class SessionEvent(Event):
    """A session lifecycle event."""

    type: str = field(default="session", init=False)
    action: str = ""
    session_id: str = ""
