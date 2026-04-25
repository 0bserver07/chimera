"""SQLite-backed event store with monotonic per-aggregate sequences.

Schema:

* ``events``        — one row per appended event (seq, aggregate_id, name,
                      version, payload_json, ts).
* ``event_sequence`` — one row per aggregate tracking ``last_seq``.

Sequences are *per-aggregate* (typically a session id) so multi-session
workloads share one database without contending for a single global
counter.  The store opens its connection in WAL mode for safe concurrent
readers, and serializes writes through a process-local lock — flock
isn't required because the WAL itself handles cross-process atomicity.

Public entry points:

* :meth:`SqliteEventStore.append` — write a typed event.
* :meth:`SqliteEventStore.read_since` — yield events for replay.
* :meth:`SqliteEventStore.replay` — drive a
  :class:`~chimera.events.sourcing.projector.ProjectorRegistry`,
  surfacing :class:`SequenceMismatchError` if the stored stream is
  inconsistent and *idempotently skipping* events the registry has
  already folded.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from chimera.events.sourcing.convert import convert_event
from chimera.events.sourcing.registry import (
    DEFAULT_REGISTRY,
    EventDefinition,
    EventRegistry,
    UnknownEventTypeError,
)

__all__ = [
    "SqliteEventStore",
    "StoredEvent",
    "SequenceMismatchError",
]


_DDL = """
CREATE TABLE IF NOT EXISTS events (
    seq            INTEGER NOT NULL,
    aggregate_id   TEXT    NOT NULL,
    name           TEXT    NOT NULL,
    version        INTEGER NOT NULL,
    payload_json   TEXT    NOT NULL,
    ts             REAL    NOT NULL,
    PRIMARY KEY (aggregate_id, seq)
);

CREATE TABLE IF NOT EXISTS event_sequence (
    aggregate_id   TEXT PRIMARY KEY,
    last_seq       INTEGER NOT NULL DEFAULT 0
);
"""


class SequenceMismatchError(Exception):
    """Raised by :meth:`SqliteEventStore.replay` when stored seq numbers
    are non-monotonic or have gaps for a given aggregate."""

    def __init__(self, aggregate_id: str, expected: int, found: int) -> None:
        self.aggregate_id = aggregate_id
        self.expected = expected
        self.found = found
        super().__init__(
            f"Sequence mismatch for {aggregate_id!r}: expected {expected}, found {found}",
        )


@dataclass
class StoredEvent:
    """On-disk envelope as returned by :meth:`SqliteEventStore.read_since`.

    Attributes:
        seq: Per-aggregate monotonic sequence number (1-based).
        aggregate_id: The aggregate (session) identifier.
        name: Logical event name.
        version: Stored schema version.
        payload: Deserialized payload (typed dataclass when the registry
            knows the type, otherwise the raw dict).
        ts: Wall-clock timestamp (``time.time()``) when the event was
            appended.
        wire_id: ``"name.version"`` after migration to latest.
    """

    seq: int
    aggregate_id: str
    name: str
    version: int
    payload: Any
    ts: float
    wire_id: str


class SqliteEventStore:
    """Append-only event store backed by a single SQLite file.

    Args:
        path: Path to the SQLite database file.  Created if missing.
            Use ``":memory:"`` for unit tests.
        registry: Event registry; defaults to :data:`DEFAULT_REGISTRY`.

    The store opens one shared connection (``check_same_thread=False``)
    and serializes writes with a :class:`threading.RLock`.  Read paths
    grab the same lock briefly to copy out rows; long-running iteration
    happens after release so subscribers don't block writers.
    """

    def __init__(
        self,
        path: str | Path,
        registry: EventRegistry | None = None,
    ) -> None:
        self._path = Path(path) if path != ":memory:" else path
        self._registry = registry or DEFAULT_REGISTRY
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we drive transactions manually
        )
        # WAL is a no-op on :memory: but harmless.
        try:
            self._conn.execute("PRAGMA journal_mode=WAL;")
        except sqlite3.DatabaseError:  # pragma: no cover — :memory: edge case
            pass
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.executescript(_DDL)

    # ------------------------------------------------------------------
    # Public: append
    # ------------------------------------------------------------------

    def append(
        self,
        aggregate_id: str,
        payload: Any,
        *,
        definition: EventDefinition | None = None,
    ) -> StoredEvent:
        """Append a typed event for *aggregate_id*.

        Args:
            aggregate_id: The aggregate (session) identifier.  Used to
                bucket sequences so multi-session DBs scale cleanly.
            payload: A dataclass instance whose type is registered in
                :attr:`registry`, OR a raw dict (in which case
                *definition* must be supplied).
            definition: Optional explicit definition.  When ``None`` the
                store looks the definition up by ``type(payload)``.

        Returns:
            The :class:`StoredEvent` envelope (with assigned ``seq``).

        Raises:
            UnknownEventTypeError: if no definition matches *payload*.
        """
        if definition is None:
            if isinstance(payload, dict):
                raise ValueError(
                    "append() requires an explicit `definition=` when payload is a dict",
                )
            definition = self._registry.find_definition_for(payload)

        if isinstance(payload, dict):
            payload_dict = dict(payload)
        else:
            payload_dict = definition.to_dict(payload)

        ts = time.time()
        with self._lock:
            cur = self._conn.execute("BEGIN IMMEDIATE;")
            try:
                row = self._conn.execute(
                    "SELECT last_seq FROM event_sequence WHERE aggregate_id = ?;",
                    (aggregate_id,),
                ).fetchone()
                last_seq = row[0] if row else 0
                seq = last_seq + 1
                self._conn.execute(
                    "INSERT INTO events "
                    "(seq, aggregate_id, name, version, payload_json, ts) "
                    "VALUES (?, ?, ?, ?, ?, ?);",
                    (
                        seq,
                        aggregate_id,
                        definition.name,
                        definition.version,
                        json.dumps(payload_dict),
                        ts,
                    ),
                )
                if row is None:
                    self._conn.execute(
                        "INSERT INTO event_sequence (aggregate_id, last_seq) VALUES (?, ?);",
                        (aggregate_id, seq),
                    )
                else:
                    self._conn.execute(
                        "UPDATE event_sequence SET last_seq = ? WHERE aggregate_id = ?;",
                        (seq, aggregate_id),
                    )
                self._conn.execute("COMMIT;")
            except Exception:
                self._conn.execute("ROLLBACK;")
                raise
            finally:
                cur.close()

        return StoredEvent(
            seq=seq,
            aggregate_id=aggregate_id,
            name=definition.name,
            version=definition.version,
            payload=payload,
            ts=ts,
            wire_id=definition.wire_id,
        )

    # ------------------------------------------------------------------
    # Public: read
    # ------------------------------------------------------------------

    def last_seq(self, aggregate_id: str) -> int:
        """Return the highest seq for *aggregate_id*, or 0 when none exists."""
        with self._lock:
            row = self._conn.execute(
                "SELECT last_seq FROM event_sequence WHERE aggregate_id = ?;",
                (aggregate_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    def read_since(
        self,
        aggregate_id: str,
        from_seq: int = 0,
    ) -> Iterator[StoredEvent]:
        """Yield stored events for *aggregate_id* with ``seq > from_seq``.

        Versions older than the latest registered are migrated forward
        through :func:`convert_event`.  Unknown event names yield raw
        :class:`StoredEvent` whose ``payload`` is the parsed dict —
        callers / projectors decide whether to skip or fail.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, name, version, payload_json, ts FROM events "
                "WHERE aggregate_id = ? AND seq > ? ORDER BY seq ASC;",
                (aggregate_id, from_seq),
            ).fetchall()

        for seq, name, version, payload_json, ts in rows:
            payload_dict = json.loads(payload_json)
            wire_id = f"{name}.{version}"
            try:
                migrated_wire, migrated_payload = convert_event(
                    wire_id, payload_dict, self._registry,
                )
                m_name, _, m_ver_str = migrated_wire.rpartition(".")
                m_ver = int(m_ver_str)
                definition = self._registry.get(m_name, m_ver)
                payload: Any = definition.from_dict(definition.payload_cls, migrated_payload)
                yield StoredEvent(
                    seq=seq, aggregate_id=aggregate_id,
                    name=m_name, version=m_ver,
                    payload=payload, ts=ts, wire_id=migrated_wire,
                )
            except UnknownEventTypeError:
                # Unknown — surface the raw dict so projectors can no-op.
                yield StoredEvent(
                    seq=seq, aggregate_id=aggregate_id,
                    name=name, version=version,
                    payload=payload_dict, ts=ts, wire_id=wire_id,
                )

    # ------------------------------------------------------------------
    # Public: replay
    # ------------------------------------------------------------------

    def replay(
        self,
        aggregate_id: str,
        registry: Any,
        from_seq: int = 0,
    ) -> int:
        """Replay events into a :class:`ProjectorRegistry`.

        Args:
            aggregate_id: The aggregate to replay.
            registry: A
                :class:`~chimera.events.sourcing.projector.ProjectorRegistry`.
            from_seq: Skip events with ``seq <= from_seq`` (used after
                snapshot restore).  The registry's per-projector cursors
                give per-projector idempotency on top of this.

        Returns:
            Number of events folded.

        Raises:
            SequenceMismatchError: if the stored stream is non-monotonic
                or has gaps (e.g. seq jumps from 3 -> 5).
        """
        events_iter = self.read_since(aggregate_id, from_seq=from_seq)

        def _generate() -> Iterator[tuple[int, str, Any]]:
            expected = from_seq + 1
            for stored in events_iter:
                if stored.seq < expected:
                    # Idempotent skip — handled by ProjectorRegistry too,
                    # but short-circuiting here saves a dispatch.
                    continue
                if stored.seq != expected:
                    raise SequenceMismatchError(
                        aggregate_id, expected=expected, found=stored.seq,
                    )
                yield stored.seq, stored.name, stored.payload
                expected = stored.seq + 1

        return int(registry.replay(_generate()))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()

    def __enter__(self) -> SqliteEventStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
