"""Command, response, and event dataclasses for the JSON-RPC server."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Base types
# ---------------------------------------------------------------------------


@dataclass
class RpcCommand:
    """Base class for all inbound RPC commands.

    Attributes:
        type: Command discriminator (e.g. ``"prompt"``, ``"cancel"``).
        id: Caller-supplied correlation ID echoed back in the response.
    """

    type: str = ""
    id: str = ""


@dataclass
class RpcEvent:
    """Base class for all outbound RPC events.

    Attributes:
        type: Event discriminator.
    """

    type: str = ""


# ---------------------------------------------------------------------------
# Commands (inbound)
# ---------------------------------------------------------------------------


@dataclass
class PromptCommand(RpcCommand):
    """Send a user message to the agent.

    Attributes:
        message: The user message to process.
    """

    type: str = "prompt"
    message: str = ""


@dataclass
class SteerCommand(RpcCommand):
    """Inject a mid-turn steering message.

    Attributes:
        message: Steering instruction inserted into the current turn.
    """

    type: str = "steer"
    message: str = ""


@dataclass
class CancelCommand(RpcCommand):
    """Cancel the in-progress agent turn."""

    type: str = "cancel"


@dataclass
class GetStateCommand(RpcCommand):
    """Request the current session state (messages, model, etc.)."""

    type: str = "get_state"


@dataclass
class CompactCommand(RpcCommand):
    """Trigger context compaction on the current session."""

    type: str = "compact"


@dataclass
class SetModelCommand(RpcCommand):
    """Switch the model used by the agent mid-session.

    Attributes:
        model: Model identifier (e.g. ``"glm-4-flash"``).
    """

    type: str = "set_model"
    model: str = ""


# ---------------------------------------------------------------------------
# Responses (outbound, reply to a specific command)
# ---------------------------------------------------------------------------


@dataclass
class RpcResponse:
    """Generic acknowledgement / error response.

    Attributes:
        command: The command type being acknowledged.
        id: Correlation ID from the originating command.
        success: Whether the command succeeded.
        error: Human-readable error description (empty on success).
    """

    command: str = ""
    id: str = ""
    success: bool = True
    error: str = ""


@dataclass
class StateResponse:
    """Response to :class:`GetStateCommand`.

    Attributes:
        id: Correlation ID from the originating command.
        messages: Conversation history as ``[{"role": ..., "content": ...}]``.
        model: Model identifier currently in use.
        command: Always ``"get_state"``.
    """

    id: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    command: str = "get_state"


# ---------------------------------------------------------------------------
# Events (outbound, unsolicited or during streaming)
# ---------------------------------------------------------------------------


@dataclass
class MessageEvent(RpcEvent):
    """Streamed content chunk or final assistant message.

    Attributes:
        role: Message role (``"assistant"``).
        content: Text content of this chunk.
        done: Whether this is the final chunk in the turn.
    """

    type: str = "message"
    role: str = "assistant"
    content: str = ""
    done: bool = False


@dataclass
class ToolExecutionEvent(RpcEvent):
    """Emitted when a tool call starts or completes.

    Attributes:
        tool_name: Name of the tool being executed.
        tool_args: Arguments passed to the tool.
        status: Execution status (``"running"``, ``"completed"``, ``"error"``).
        result: Tool output (populated on completion).
    """

    type: str = "tool_execution"
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    status: str = "running"
    result: str | None = None


@dataclass
class ErrorEvent(RpcEvent):
    """Emitted when an unhandled error occurs.

    Attributes:
        message: Human-readable error description.
    """

    type: str = "error"
    message: str = ""
