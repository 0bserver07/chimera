"""Append-only, indexed, crash-recoverable event log."""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
import uuid
import warnings
from pathlib import Path
from typing import Any

from chimera.events.base import Event

__all__ = ["EventLog"]

FILE_PATTERN = "event-{idx:06d}-{event_id}.json"
_FILE_RE = re.compile(r"^event-(\d{6})-([0-9a-f]{8})\.json$")
LOCK_TIMEOUT = 30


class _FileLock:
    """flock-based file lock with timeout.

    Args:
        lock_path: Path to the lock file.
        timeout: Maximum seconds to wait for the lock.
    """

    def __init__(self, lock_path: str | Path, timeout: float = LOCK_TIMEOUT) -> None:
        self._lock_path = Path(lock_path)
        self._timeout = timeout
        self._fd: int | None = None

    def __enter__(self) -> _FileLock:
        self._fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR)
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(self._fd)
                    self._fd = None
                    raise TimeoutError(
                        f"Could not acquire lock on {self._lock_path} "
                        f"within {self._timeout}s"
                    )
                time.sleep(0.01)

    def __exit__(self, *args: object) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


class EventLog:
    """Append-only, indexed, crash-recoverable event log.

    Events are persisted as individual JSON files on disk, named with a
    sequential index and a short unique ID.  The log supports efficient
    retrieval by index or ID, range queries, and automatic gap detection
    on startup.

    Args:
        directory: Directory where event files are stored.  Created if it
            does not exist.
    """

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._events_by_index: dict[int, Event] = {}
        self._events_by_id: dict[str, Event] = {}
        self._index_to_id: dict[int, str] = {}
        self._next_index: int = 0
        self._scan_and_load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, event: Event) -> int:
        """Append *event* to the log and return its index.

        The event is written to disk under a file lock before the
        in-memory indices are updated, ensuring crash recoverability.

        Args:
            event: The event to persist.

        Returns:
            The zero-based index assigned to the event.
        """
        event_id = event.metadata.get("event_id") or self._generate_id()
        # Store event_id in metadata so it survives serialization roundtrip.
        event.metadata["event_id"] = event_id

        with self._file_lock():
            idx = self._next_index
            filename = FILE_PATTERN.format(idx=idx, event_id=event_id)
            filepath = self._dir / filename
            data = self._serialize(event, idx, event_id)
            filepath.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self._events_by_index[idx] = event
            self._events_by_id[event_id] = event
            self._index_to_id[idx] = event_id
            self._next_index = idx + 1
        return idx

    def get_by_index(self, idx: int) -> Event | None:
        """Return the event at *idx*, or ``None`` if it does not exist."""
        return self._events_by_index.get(idx)

    def get_by_id(self, event_id: str) -> Event | None:
        """Return the event with *event_id*, or ``None`` if not found."""
        return self._events_by_id.get(event_id)

    def get_range(self, start: int = 0, end: int | None = None) -> list[Event]:
        """Return events in the half-open range ``[start, end)``.

        Args:
            start: First index (inclusive, default ``0``).
            end: Past-the-end index (exclusive).  ``None`` means all
                events from *start* onward.

        Returns:
            A list of events in index order.
        """
        if end is None:
            end = self._next_index
        return [
            self._events_by_index[i]
            for i in range(start, end)
            if i in self._events_by_index
        ]

    def get_since(self, idx: int) -> list[Event]:
        """Return all events from *idx* onward (inclusive).

        Args:
            idx: Starting index (inclusive).

        Returns:
            A list of events in index order.
        """
        return self.get_range(start=idx)

    @property
    def length(self) -> int:
        """Number of events currently stored."""
        return len(self._events_by_index)

    @property
    def last_index(self) -> int:
        """Index of the most recent event, or ``-1`` if the log is empty."""
        if not self._events_by_index:
            return -1
        return max(self._events_by_index.keys())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scan_and_load(self) -> None:
        """Scan the log directory and rebuild in-memory indices.

        Detects and warns about index gaps caused by missing files.
        """
        entries: list[tuple[int, str, Path]] = []
        for child in sorted(self._dir.iterdir()):
            m = _FILE_RE.match(child.name)
            if m:
                entries.append((int(m.group(1)), m.group(2), child))

        if not entries:
            self._next_index = 0
            return

        gaps: list[int] = []
        expected = entries[0][0]
        for idx, event_id, filepath in entries:
            while expected < idx:
                gaps.append(expected)
                expected += 1
            try:
                raw = json.loads(filepath.read_text(encoding="utf-8"))
                event = self._deserialize(raw)
                self._events_by_index[idx] = event
                self._events_by_id[event_id] = event
                self._index_to_id[idx] = event_id
            except (json.JSONDecodeError, KeyError) as exc:
                warnings.warn(
                    f"Skipping corrupt event file {filepath.name}: {exc}",
                    stacklevel=2,
                )
            expected = idx + 1

        self._next_index = entries[-1][0] + 1

        if gaps:
            self._report_gaps(gaps)

    def _file_lock(self) -> _FileLock:
        """Return a :class:`_FileLock` for the log directory."""
        return _FileLock(self._dir / ".lock")

    @staticmethod
    def _serialize(event: Event, idx: int, event_id: str) -> dict[str, Any]:
        """Serialize an event to a JSON-compatible dict.

        Args:
            event: The event to serialize.
            idx: The sequential index.
            event_id: The unique short ID.

        Returns:
            A dict ready for ``json.dumps``.
        """
        return {
            "idx": idx,
            "event_id": event_id,
            "type": event.type,
            "timestamp": event.timestamp,
            "metadata": event.metadata,
        }

    @staticmethod
    def _deserialize(data: dict[str, Any]) -> Event:
        """Reconstruct an :class:`Event` from a serialized dict.

        Args:
            data: Dict previously produced by :meth:`_serialize`.

        Returns:
            A new :class:`Event` instance.
        """
        return Event(
            type=data["type"],
            timestamp=data["timestamp"],
            metadata=data.get("metadata", {}),
        )

    @staticmethod
    def _generate_id() -> str:
        """Generate a short unique ID (first 8 hex chars of a UUID4)."""
        return uuid.uuid4().hex[:8]

    @staticmethod
    def _report_gaps(gaps: list[int]) -> None:
        """Warn about missing event indices.

        Args:
            gaps: List of missing indices.
        """
        warnings.warn(
            f"Event log has gaps at indices: {gaps}. "
            "Some events may have been lost.",
            stacklevel=3,
        )
