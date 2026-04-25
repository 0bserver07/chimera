"""JSONL export / import round-trip for the SQLite event store.

The on-disk JSON line format is one event per line:

.. code-block:: json

    {"seq": 1, "aggregate_id": "s-abc", "wire_id": "tool.called.1",
     "ts": 1714150000.123, "payload": {...}}

Why JSONL:

* trivially diffable / greppable for debugging,
* streams cleanly through pipes (``chimera ... | jq``),
* readable on systems without SQLite tooling installed.

Imports go through :func:`convert_event` so older-version logs land in
the latest schema.  When the destination store already contains events
for the aggregate, :func:`replay_from_jsonl` *idempotently skips* any
``seq`` that has already been written.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Iterable, TextIO

from chimera.events.sourcing.convert import convert_event
from chimera.events.sourcing.registry import (
    EventRegistry,
    UnknownEventTypeError,
)
from chimera.events.sourcing.sqlite_store import (
    SequenceMismatchError,
    SqliteEventStore,
)

__all__ = ["export_jsonl", "replay_from_jsonl"]


def export_jsonl(
    store: SqliteEventStore,
    aggregate_id: str,
    out: str | Path | TextIO,
    *,
    from_seq: int = 0,
) -> int:
    """Write all events for *aggregate_id* (with ``seq > from_seq``) as JSONL.

    Args:
        store: Source :class:`SqliteEventStore`.
        aggregate_id: Aggregate to export.
        out: File path or open text stream.  When a path is given a new
            file is created (truncating any existing).
        from_seq: Skip events with ``seq <= from_seq`` (default 0 = all).

    Returns:
        The number of lines written.
    """
    close_after = False
    fh: TextIO
    if isinstance(out, (str, Path)):
        fh = Path(out).open("w", encoding="utf-8")
        close_after = True
    else:
        fh = out

    written = 0
    try:
        for stored in store.read_since(aggregate_id, from_seq=from_seq):
            # Re-serialize the (possibly migrated) payload through the
            # definition's to_dict if it's a typed instance.
            if isinstance(stored.payload, dict):
                payload_dict = dict(stored.payload)
            else:
                definition = store._registry.find_definition_for(stored.payload)  # noqa: SLF001
                payload_dict = definition.to_dict(stored.payload)
            line = json.dumps(
                {
                    "seq": stored.seq,
                    "aggregate_id": stored.aggregate_id,
                    "wire_id": stored.wire_id,
                    "ts": stored.ts,
                    "payload": payload_dict,
                },
                ensure_ascii=False,
            )
            fh.write(line)
            fh.write("\n")
            written += 1
    finally:
        if close_after:
            fh.close()
    return written


def _read_jsonl(source: str | Path | TextIO | Iterable[str]) -> Iterable[str]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                yield line
    elif isinstance(source, io.IOBase):
        for line in source:
            yield line
    else:
        for line in source:
            yield line


def replay_from_jsonl(
    source: str | Path | TextIO | Iterable[str],
    store: SqliteEventStore,
    *,
    registry: EventRegistry | None = None,
    strict: bool = False,
) -> int:
    """Re-append JSONL events into *store*.

    Existing seq numbers in *store* are honored: lines whose ``seq`` is
    ``<= store.last_seq(aggregate_id)`` are silently skipped (idempotent
    import).  When the very next seq would create a gap (e.g. store is
    at seq=3 and the file's next line is seq=10), behaviour depends on
    ``strict``:

    * ``strict=False`` (default): the file's seq is ignored and events
      are simply appended in order.  Useful for "merge two histories".
    * ``strict=True``: a :class:`SequenceMismatchError` is raised.

    Args:
        source: File path, open text stream, or iterable of JSON lines.
        store: Destination store.
        registry: Override registry (defaults to ``store._registry``).
        strict: Enforce that the file's seq numbers continue the
            store's sequence exactly.

    Returns:
        The number of events appended.
    """
    reg = registry or store._registry  # noqa: SLF001
    appended = 0

    seen_aggregates: dict[str, int] = {}
    for raw in _read_jsonl(source):
        line = raw.strip()
        if not line:
            continue
        record = json.loads(line)
        aggregate_id = record["aggregate_id"]
        wire_id = record["wire_id"]
        payload = record["payload"]
        file_seq = int(record["seq"])

        if aggregate_id not in seen_aggregates:
            seen_aggregates[aggregate_id] = store.last_seq(aggregate_id)
        store_last = seen_aggregates[aggregate_id]

        if file_seq <= store_last:
            # Idempotent skip — already imported.
            continue

        try:
            migrated_wire, migrated_payload = convert_event(wire_id, payload, reg)
        except UnknownEventTypeError:
            if strict:
                raise
            # Unknown type: skip silently.
            continue

        name, _, ver_str = migrated_wire.rpartition(".")
        version = int(ver_str)
        definition = reg.get(name, version)

        if strict and file_seq != store_last + 1:
            raise SequenceMismatchError(
                aggregate_id, expected=store_last + 1, found=file_seq,
            )

        # Rebuild the typed instance for store.append's lookup; the
        # store re-serializes via to_dict so this round-trip is exact.
        instance = definition.from_dict(definition.payload_cls, migrated_payload)
        store.append(aggregate_id, instance, definition=definition)
        seen_aggregates[aggregate_id] = store.last_seq(aggregate_id)
        appended += 1

    return appended
