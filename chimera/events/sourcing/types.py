"""Concrete event types for the event-sourcing subsystem.

These are the 12 canonical event kinds from the spec used by the
:class:`SqliteEventStore`, :class:`ProjectorRegistry`, and the JSONL
export/import path.

Every type here is *versioned*: the registry stores ``(name, version)``
keys and the wire form uses ``"{name}.{version}"`` so that
:func:`chimera.events.sourcing.convert.convert_event` can migrate older
payloads forward when the schema evolves.

The classes are intentionally plain dataclasses — they are agnostic of
:class:`chimera.events.base.Event`'s monotonic ``timestamp`` semantics
because event-sourced state needs *wall-clock* ordering for replay
across processes.  ``StoredEvent`` (in
:mod:`chimera.events.sourcing.sqlite_store`) is the on-disk envelope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "SessionCreatedEvent",
    "SessionEndedEvent",
    "ToolCalledEvent",
    "ToolCompletedEvent",
    "FileMutatedEvent",
    "PermissionDecidedEvent",
    "ModelRequestedEvent",
    "ModelRespondedEvent",
    "CompactionPerformedEvent",
    "ErrorOccurredEvent",
    "UserMessageEvent",
    "AgentResultEvent",
]


@dataclass
class SessionCreatedEvent:
    """A new session was created.

    Attributes:
        session_id: The session identifier.
        agent_name: Friendly name of the agent driving the session.
        model: Provider model name at session creation time.
        metadata: Free-form dict for plugin-specific fields.
    """

    session_id: str = ""
    agent_name: str = ""
    model: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionEndedEvent:
    """A session was closed (either cleanly or with an error)."""

    session_id: str = ""
    success: bool = True
    error: str | None = None
    total_steps: int = 0
    total_cost: float = 0.0


@dataclass
class ToolCalledEvent:
    """A tool was dispatched."""

    session_id: str = ""
    call_id: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCompletedEvent:
    """A tool call finished (success or error)."""

    session_id: str = ""
    call_id: str = ""
    tool_name: str = ""
    success: bool = True
    output: str = ""
    error: str | None = None


@dataclass
class FileMutatedEvent:
    """A file on disk was created, modified, or deleted by a tool."""

    session_id: str = ""
    call_id: str = ""
    path: str = ""
    operation: str = "modified"  # "created" | "modified" | "deleted"


@dataclass
class PermissionDecidedEvent:
    """A permission policy issued a decision for a tool call."""

    session_id: str = ""
    call_id: str = ""
    tool_name: str = ""
    action: str = ""  # "allow" | "deny" | "ask"
    granted: bool = False


@dataclass
class ModelRequestedEvent:
    """A request was sent to the LLM provider."""

    session_id: str = ""
    model: str = ""
    message_count: int = 0
    tool_count: int = 0


@dataclass
class ModelRespondedEvent:
    """A response was received from the LLM provider."""

    session_id: str = ""
    model: str = ""
    content_length: int = 0
    tool_calls_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class CompactionPerformedEvent:
    """Context compaction reduced the message count."""

    session_id: str = ""
    messages_before: int = 0
    messages_after: int = 0
    strategy: str = ""


@dataclass
class ErrorOccurredEvent:
    """A recoverable or fatal error occurred during agent execution."""

    session_id: str = ""
    error: str = ""
    recoverable: bool = True
    where: str = ""  # tool name / loop phase / etc.


@dataclass
class UserMessageEvent:
    """The user supplied a turn-starting message."""

    session_id: str = ""
    content: str = ""


@dataclass
class AgentResultEvent:
    """The agent finished a turn and produced output."""

    session_id: str = ""
    output: str = ""
    steps: int = 0
    tool_calls_total: int = 0
    cost: float = 0.0
    success: bool = True
    error: str | None = None
