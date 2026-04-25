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
    "ModelRequestEvent",
    "ModelResponseEvent",
    "TurnStartEvent",
    "TurnEndEvent",
    "StreamStartEvent",
    "StreamEndEvent",
    "AgentStartEvent",
    "AgentEndEvent",
    "SteeringEvent",
    "CancellationEvent",
    "TodoWriteEvent",
    "TeammateIdleEvent",
    "TaskCreatedEvent",
    "TaskCompletedEvent",
    "TeammateMessageEvent",
    "HookUpdatedInputEvent",
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
    call_id: str = ""


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


@dataclass
class ModelRequestEvent(Event):
    """About to send a request to the LLM provider."""

    type: str = field(default="model_request", init=False)
    model: str = ""
    message_count: int = 0
    tool_count: int = 0


@dataclass
class ModelResponseEvent(Event):
    """Received a response from the LLM provider."""

    type: str = field(default="model_response", init=False)
    model: str = ""
    content_length: int = 0
    tool_calls_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class TurnStartEvent(Event):
    """A new agent turn is starting."""

    type: str = field(default="turn_start", init=False)
    turn_number: int = 0


@dataclass
class TurnEndEvent(Event):
    """An agent turn has completed."""

    type: str = field(default="turn_end", init=False)
    turn_number: int = 0
    tool_calls_count: int = 0


@dataclass
class StreamStartEvent(Event):
    """Streaming response has started."""

    type: str = field(default="stream_start", init=False)
    model: str = ""


@dataclass
class StreamEndEvent(Event):
    """Streaming response has completed."""

    type: str = field(default="stream_end", init=False)
    total_tokens: int = 0


@dataclass
class AgentStartEvent(Event):
    """Agent loop has started."""

    type: str = field(default="agent_start", init=False)
    max_steps: int = 0


@dataclass
class AgentEndEvent(Event):
    """Agent loop has completed."""

    type: str = field(default="agent_end", init=False)
    steps: int = 0
    success: bool = True
    total_cost: float = 0.0


@dataclass
class SteeringEvent(Event):
    """A steering message was injected."""

    type: str = field(default="steering", init=False)
    content: str = ""


@dataclass
class CancellationEvent(Event):
    """The agent was cancelled."""

    type: str = field(default="cancellation", init=False)
    at_step: int = 0


@dataclass
class TeammateIdleEvent(Event):
    """A teammate has finished its task list and is idle."""

    type: str = field(default="teammate_idle", init=False)
    team: str = ""
    agent_id: str = ""


@dataclass
class TaskCreatedEvent(Event):
    """A new task was added to the team queue."""

    type: str = field(default="task_created", init=False)
    team: str = ""
    task_id: str = ""
    description: str = ""
    created_by: str = ""


@dataclass
class TaskCompletedEvent(Event):
    """A teammate completed a task."""

    type: str = field(default="task_completed", init=False)
    team: str = ""
    task_id: str = ""
    agent_id: str = ""
    result: str = ""


@dataclass
class TeammateMessageEvent(Event):
    """A teammate received a direct message."""

    type: str = field(default="teammate_message", init=False)
    team: str = ""
    sender: str = ""
    recipient: str = ""
    content: str = ""


@dataclass
class TodoWriteEvent(Event):
    """A durable record of every TodoTool mutation.

    Emitted once per ``add`` / ``complete`` / ``set`` / ``remove`` call so
    that an :class:`EventSourcedSession` can replay the log and
    reconstitute the agent's task list across restarts and compaction.

    Attributes:
        todos: Snapshot of the full todo list after the mutation.  Each
            entry is a JSON-safe dict with ``id``, ``task``, and ``done``
            keys.
        op: The mutation kind — one of ``"add"``, ``"complete"``,
            ``"set"``, or ``"remove"``.  Replay uses ``"set"`` for full
            state restore and the others for incremental application.
        session_id: The session that produced the write, so multi-session
            event logs can be filtered correctly during replay.
    """

    type: str = field(default="todo_write", init=False)
    todos: list[dict[str, Any]] = field(default_factory=list)
    op: str = "set"
    session_id: str = ""


@dataclass
class HookUpdatedInputEvent(Event):
    """A PreToolUse hook mutated a tool call's input arguments.

    Emitted by the tool executor whenever a hook returns
    ``hookSpecificOutput.updatedInput`` and the mutated arguments are
    applied to the dispatched tool call.

    Attributes:
        tool_name: The tool that was about to be invoked.
        call_id: The tool call identifier.
        original: The original arguments dict the model produced.
        updated: The merged arguments dict that was actually dispatched
            (hook keys override originals; non-overridden keys preserved).
    """

    type: str = field(default="hook_updated_input", init=False)
    tool_name: str = ""
    call_id: str = ""
    original: dict[str, Any] = field(default_factory=dict)
    updated: dict[str, Any] = field(default_factory=dict)
