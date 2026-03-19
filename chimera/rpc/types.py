"""RPC command, response, and event types."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# --- Commands (client -> server) ---

@dataclass
class RpcCommand:
    """Base for all RPC commands."""
    type: str = ""
    id: str = ""


@dataclass
class PromptCommand(RpcCommand):
    type: str = "prompt"
    message: str = ""
    images: list[dict[str, str]] = field(default_factory=list)


@dataclass
class SteerCommand(RpcCommand):
    type: str = "steer"
    message: str = ""


@dataclass
class CompactCommand(RpcCommand):
    type: str = "compact"
    instructions: str = ""


@dataclass
class GetStateCommand(RpcCommand):
    type: str = "get_state"


@dataclass
class CancelCommand(RpcCommand):
    type: str = "cancel"


@dataclass
class SetModelCommand(RpcCommand):
    type: str = "set_model"
    provider: str = ""
    model: str = ""


# --- Responses (server -> client) ---

@dataclass
class RpcResponse:
    """Base for all RPC responses."""
    command: str = ""
    id: str = ""
    success: bool = True
    error: str = ""


@dataclass
class StateResponse(RpcResponse):
    command: str = "get_state"
    messages: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    total_cost: float = 0.0
    context_tokens: int = 0


# --- Events (server -> client, unsolicited) ---

@dataclass
class RpcEvent:
    """Base for all RPC events."""
    type: str = ""


@dataclass
class MessageEvent(RpcEvent):
    type: str = "message"
    role: str = ""
    content: str = ""
    done: bool = False


@dataclass
class TextDeltaEvent(RpcEvent):
    type: str = "text_delta"
    content: str = ""


@dataclass
class ToolExecutionEvent(RpcEvent):
    type: str = "tool_execution"
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    result: str = ""
    success: bool = True
    phase: str = ""


@dataclass
class CompactionEvent(RpcEvent):
    type: str = "compaction"
    tokens_before: int = 0
    tokens_after: int = 0


@dataclass
class ErrorEvent(RpcEvent):
    type: str = "error"
    message: str = ""
