# chimera/events/types.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chimera.events.base import Event

__all__ = [
    "CriticEvent",
    "ExternalAgentCompleteEvent",
    "ExternalAgentStartEvent",
    "ExternalAgentToolCallEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "StepEvent",
    "TextDeltaEvent",
    "ErrorEvent",
    "LoopDetectedEvent",
    "CompactionEvent",
    "PermissionEvent",
    "SecurityEvent",
    "SessionEvent",
    "StepCostEvent",
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


@dataclass
class CriticEvent(Event):
    """A critic evaluation was performed."""

    type: str = field(default="critic", init=False)
    score: float = 0.0
    passed: bool = False
    feedback: str | None = None
    iteration: int = 0


@dataclass
class ExternalAgentStartEvent(Event):
    """An external agent task was started."""

    type: str = field(default="external_agent_start", init=False)
    agent_name: str = ""
    task: str = ""


@dataclass
class ExternalAgentCompleteEvent(Event):
    """An external agent task completed."""

    type: str = field(default="external_agent_complete", init=False)
    agent_name: str = ""
    response_text: str = ""
    cost: float = 0.0
    tool_calls_count: int = 0


@dataclass
class ExternalAgentToolCallEvent(Event):
    """An external agent made a tool call."""

    type: str = field(default="external_agent_tool_call", init=False)
    agent_name: str = ""
    tool_call_id: str = ""
    title: str = ""
    status: str = ""


@dataclass
class SecurityEvent(Event):
    """A security analysis decision was made for a tool call."""

    type: str = field(default="security", init=False)
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    risk: str = ""
    action: str = ""  # "blocked", "confirmed", "allowed"


@dataclass
class StepCostEvent(Event):
    """Cost and token usage for an agent step."""

    type: str = field(default="step_cost", init=False)
    step_index: int = 0
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_hit_rate: float = 0.0
    duration: float = 0.0
