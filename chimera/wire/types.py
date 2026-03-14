from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class WireMessage:
    """Base class for all wire messages."""
    pass

@dataclass
class WireRequest(WireMessage):
    """A message that expects a response."""
    request_id: str = ""
    timeout: float = 30.0

@dataclass
class WireResponse(WireMessage):
    """Response to a WireRequest."""
    request_id: str = ""

# -- Lifecycle events --

@dataclass
class TurnBegin(WireMessage):
    """Emitted when a new agent turn starts."""
    turn_id: int = 0

@dataclass
class TurnEnd(WireMessage):
    """Emitted when an agent turn completes."""
    turn_id: int = 0
    steps: int = 0
    output: str = ""

@dataclass
class StepBegin(WireMessage):
    """Emitted when an agent step starts."""
    step: int = 0

@dataclass
class StepEnd(WireMessage):
    """Emitted when an agent step completes."""
    step: int = 0
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: str | None = None

# -- Request/Response pairs --

@dataclass
class ApprovalRequest(WireRequest):
    """Request approval for a tool call."""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)

@dataclass
class ApprovalResponse(WireResponse):
    """Response to an approval request."""
    approved: bool = True
    reason: str = ""

@dataclass
class UserQuestion(WireRequest):
    """Ask the user a question."""
    question: str = ""
    choices: list[str] | None = None

@dataclass
class UserAnswer(WireResponse):
    """User's answer to a question."""
    answer: str = ""

# -- Status --

@dataclass
class StatusUpdate(WireMessage):
    """Agent status update."""
    context_tokens: int = 0
    max_tokens: int = 0
    total_cost: float = 0.0
    step: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
