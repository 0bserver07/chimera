"""Versioned event migration via ``convert_event``.

The store reads a wire identifier ``"{name}.{version}"`` and a raw
payload dict.  When the stored version is older than the latest
registered version, :func:`convert_event` walks the chain of
``upgrade_from`` callables on each :class:`EventDefinition` to produce a
payload that fits the latest schema.

If no upgrade chain exists, the function returns the original wire id +
payload unchanged so legacy events remain replayable as long as the
:class:`Projector` understands them.
"""

from __future__ import annotations

from typing import Any

from chimera.events.sourcing.registry import (
    DEFAULT_REGISTRY,
    EventRegistry,
    UnknownEventTypeError,
)

__all__ = ["convert_event", "ConvertError"]


class ConvertError(Exception):
    """Raised when a migration chain is broken (missing intermediate version)."""


def convert_event(
    wire_id: str,
    payload: dict[str, Any],
    registry: EventRegistry | None = None,
) -> tuple[str, dict[str, Any]]:
    """Migrate *payload* forward to the latest version registered for its name.

    Args:
        wire_id: The stored identifier ``"{name}.{version}"``.
        payload: The serialized payload dict.
        registry: Registry to consult; defaults to :data:`DEFAULT_REGISTRY`.

    Returns:
        ``(latest_wire_id, migrated_payload)``.  When *wire_id* is
        already at the latest version, the input is returned verbatim
        (after a defensive copy of *payload*).

    Raises:
        UnknownEventTypeError: if *wire_id* is not registered at all.
        ConvertError: if an intermediate version in the upgrade chain has
            no registered ``upgrade_from`` entry.
    """
    reg = registry or DEFAULT_REGISTRY

    name, _, ver_str = wire_id.rpartition(".")
    if not name or not ver_str:
        raise UnknownEventTypeError(wire_id)

    try:
        from_version = int(ver_str)
    except ValueError as exc:
        raise UnknownEventTypeError(wire_id) from exc

    latest = reg.latest_version(name)
    if from_version == latest:
        return wire_id, dict(payload)
    if from_version > latest:
        # Downgrade not supported — caller has a newer schema than this process.
        raise ConvertError(
            f"Stored event {wire_id!r} is newer than registered latest {name}.{latest}",
        )

    current_payload: dict[str, Any] = dict(payload)
    current_version = from_version
    while current_version < latest:
        next_version = current_version + 1
        try:
            target_def = reg.get(name, next_version)
        except UnknownEventTypeError as exc:
            raise ConvertError(
                f"No definition registered for intermediate version {name}.{next_version}",
            ) from exc
        upgrade_fn = target_def.upgrade_from.get(current_version)
        if upgrade_fn is None:
            raise ConvertError(
                f"No upgrade path from {name}.{current_version} to {name}.{next_version}",
            )
        current_payload = upgrade_fn(current_payload)
        current_version = next_version

    return f"{name}.{latest}", current_payload
