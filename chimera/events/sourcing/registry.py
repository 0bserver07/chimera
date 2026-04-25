"""Typed event registry with versioned ``"{name}.{version}"`` identifiers.

Each :class:`EventDefinition` binds a logical event name + version to:

* the dataclass ``payload_cls`` used in-process,
* a ``to_dict`` / ``from_dict`` pair used by the SQLite store and the
  JSONL export,
* an optional ``upgrade_from`` mapping declaring how older versions are
  migrated forward (consumed by
  :func:`chimera.events.sourcing.convert.convert_event`).

The default :data:`DEFAULT_REGISTRY` is preloaded with all 12 types from
:mod:`chimera.events.sourcing.types` at version 1.  Plugins or future
schema bumps register new versions via :meth:`EventRegistry.register`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any, Callable

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
    "EventDefinition",
    "EventRegistry",
    "DEFAULT_REGISTRY",
    "UnknownEventTypeError",
]


class UnknownEventTypeError(KeyError):
    """Raised when a wire identifier (``"name.version"``) has no definition."""


def _default_to_dict(payload: Any) -> dict[str, Any]:
    if not is_dataclass(payload) or isinstance(payload, type):
        raise TypeError(
            f"Default to_dict only supports dataclass instances; got {type(payload)!r}",
        )
    return asdict(payload)


def _default_from_dict(cls: type, data: dict[str, Any]) -> Any:
    """Build *cls* from *data*, ignoring extra keys.

    Mirrors the lenient deserialization used elsewhere in Chimera so
    forward-compatible payloads (extra fields) don't crash replay.
    """
    if not is_dataclass(cls):
        raise TypeError(f"Default from_dict only supports dataclass types; got {cls!r}")
    valid = {f.name for f in fields(cls)}
    kwargs = {k: v for k, v in data.items() if k in valid}
    return cls(**kwargs)


@dataclass
class EventDefinition:
    """Schema descriptor for one ``(name, version)`` pair.

    Attributes:
        name: Logical event name (e.g. ``"tool.called"``).
        version: Schema version (monotonically increasing per name).
        payload_cls: The Python type carrying the payload.
        to_dict: Serializer; default uses :func:`dataclasses.asdict`.
        from_dict: Deserializer; default rebuilds the dataclass and
            tolerates unknown keys.
        upgrade_from: Optional mapping ``{old_version: upgrade_fn}`` —
            consulted by :func:`convert_event` when the stored version is
            older than the latest registered version.
    """

    name: str
    version: int
    payload_cls: type
    to_dict: Callable[[Any], dict[str, Any]] = _default_to_dict
    from_dict: Callable[[type, dict[str, Any]], Any] = _default_from_dict
    upgrade_from: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = field(
        default_factory=dict,
    )

    @property
    def wire_id(self) -> str:
        """``"{name}.{version}"`` — the on-disk + JSONL identifier."""
        return f"{self.name}.{self.version}"


class EventRegistry:
    """Mapping from ``(name, version)`` (and ``"name.version"``) to definitions.

    Concurrency: registration is *not* thread-safe; load all definitions
    at startup, then treat the registry as read-only for the rest of the
    process.  The runtime lookups (:meth:`get`, :meth:`latest_version`)
    are pure dict reads.
    """

    def __init__(self) -> None:
        self._by_wire: dict[str, EventDefinition] = {}
        # name -> sorted list of versions
        self._versions: dict[str, list[int]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, definition: EventDefinition) -> None:
        """Register *definition*.

        Raises:
            ValueError: if ``(name, version)`` is already registered.
        """
        if definition.wire_id in self._by_wire:
            raise ValueError(f"Event {definition.wire_id!r} already registered")
        self._by_wire[definition.wire_id] = definition
        versions = self._versions.setdefault(definition.name, [])
        versions.append(definition.version)
        versions.sort()

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str, version: int) -> EventDefinition:
        """Return the definition for ``(name, version)``.

        Raises:
            UnknownEventTypeError: if no such definition exists.
        """
        wire = f"{name}.{version}"
        try:
            return self._by_wire[wire]
        except KeyError as exc:
            raise UnknownEventTypeError(wire) from exc

    def get_by_wire(self, wire_id: str) -> EventDefinition:
        """Lookup by the wire identifier (``"name.version"``)."""
        try:
            return self._by_wire[wire_id]
        except KeyError as exc:
            raise UnknownEventTypeError(wire_id) from exc

    def latest_version(self, name: str) -> int:
        """Return the highest registered version for *name*."""
        versions = self._versions.get(name)
        if not versions:
            raise UnknownEventTypeError(name)
        return versions[-1]

    def all_versions(self, name: str) -> list[int]:
        """Return every registered version for *name* (ascending)."""
        return list(self._versions.get(name, ()))

    def names(self) -> list[str]:
        """Return all registered event names."""
        return sorted(self._versions)

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def find_definition_for(self, payload: Any) -> EventDefinition:
        """Return the definition whose ``payload_cls`` matches *payload* (latest version).

        Used by the store to serialize a Python instance without forcing
        the caller to also pass the wire identifier.
        """
        cls = type(payload)
        for definition in reversed(list(self._by_wire.values())):
            if definition.payload_cls is cls:
                return definition
        raise UnknownEventTypeError(cls.__name__)


# ---------------------------------------------------------------------------
# Default registry — preloaded with the 12 spec event types at v1.
# ---------------------------------------------------------------------------

DEFAULT_REGISTRY = EventRegistry()

for _name, _cls in [
    ("session.created", SessionCreatedEvent),
    ("session.ended", SessionEndedEvent),
    ("tool.called", ToolCalledEvent),
    ("tool.completed", ToolCompletedEvent),
    ("file.mutated", FileMutatedEvent),
    ("permission.decided", PermissionDecidedEvent),
    ("model.requested", ModelRequestedEvent),
    ("model.responded", ModelRespondedEvent),
    ("compaction.performed", CompactionPerformedEvent),
    ("error.occurred", ErrorOccurredEvent),
    ("user.message", UserMessageEvent),
    ("agent.result", AgentResultEvent),
]:
    DEFAULT_REGISTRY.register(
        EventDefinition(name=_name, version=1, payload_cls=_cls),
    )
