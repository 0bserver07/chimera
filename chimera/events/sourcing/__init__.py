"""Event-sourcing subsystem (issue #128).

This package layers a typed event registry, projector framework, and
SQLite-backed durable store on top of the existing in-memory
:class:`chimera.events.base.EventBus`.  The legacy
:mod:`chimera.sessions.eventlog` (per-event JSONL store) keeps working
unchanged — :class:`SqliteEventStore` is an additional persistence
strategy, not a replacement.

Public surface:

* :class:`EventDefinition`, :class:`EventRegistry`, :data:`DEFAULT_REGISTRY`
* :class:`Projector`, :class:`ProjectorRegistry`
* :class:`SqliteEventStore`, :class:`StoredEvent`, :class:`SequenceMismatchError`
* :func:`convert_event`
* :func:`export_jsonl`, :func:`replay_from_jsonl`
* The 12 spec event payload types (re-exported from
  :mod:`chimera.events.sourcing.types`).
* :class:`EventSourcingSink` — an :class:`~chimera.events.base.EventBus`
  subscriber that translates classic events into typed sourced events
  and persists them to a :class:`SqliteEventStore`.
"""

from __future__ import annotations

from chimera.events.sourcing.convert import ConvertError, convert_event
from chimera.events.sourcing.export import export_jsonl, replay_from_jsonl
from chimera.events.sourcing.projector import (
    Projector,
    ProjectorError,
    ProjectorRegistry,
    ProjectorState,
)
from chimera.events.sourcing.registry import (
    DEFAULT_REGISTRY,
    EventDefinition,
    EventRegistry,
    UnknownEventTypeError,
)
from chimera.events.sourcing.sink import EventSourcingSink
from chimera.events.sourcing.sqlite_store import (
    SequenceMismatchError,
    SqliteEventStore,
    StoredEvent,
)
from chimera.events.sourcing.types import (
    AgentResultEvent,
    CompactionPerformedEvent,
    ErrorOccurredEvent,
    FileMutatedEvent,
    ModelRequestedEvent,
    ModelRespondedEvent,
    PermissionDecidedEvent,
    SessionCreatedEvent,
    SessionEndedEvent,
    ToolCalledEvent,
    ToolCompletedEvent,
    UserMessageEvent,
)

__all__ = [
    "DEFAULT_REGISTRY",
    "AgentResultEvent",
    "CompactionPerformedEvent",
    "ConvertError",
    "ErrorOccurredEvent",
    "EventDefinition",
    "EventRegistry",
    "EventSourcingSink",
    "FileMutatedEvent",
    "ModelRequestedEvent",
    "ModelRespondedEvent",
    "PermissionDecidedEvent",
    "Projector",
    "ProjectorError",
    "ProjectorRegistry",
    "ProjectorState",
    "SequenceMismatchError",
    "SessionCreatedEvent",
    "SessionEndedEvent",
    "SqliteEventStore",
    "StoredEvent",
    "ToolCalledEvent",
    "ToolCompletedEvent",
    "UnknownEventTypeError",
    "UserMessageEvent",
    "convert_event",
    "export_jsonl",
    "replay_from_jsonl",
]
