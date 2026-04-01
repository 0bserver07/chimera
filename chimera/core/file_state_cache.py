"""LRU cache for file contents keyed by (path, offset, limit).

# Integration: Pass FileStateCache to ReadFileTool and check cache.get()
# before reading. If entry exists and mtime matches, return
# "[File unchanged since last read]" stub instead of re-reading.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileStateEntry:
    """A cached snapshot of a file's content."""

    content: str
    mtime: float
    offset: int | None
    limit: int | None
    size: int


class FileStateCache:
    """LRU cache mapping ``(path, offset, limit)`` to :class:`FileStateEntry`.

    When the number of entries exceeds *max_entries* the least-recently-used
    entry is evicted.
    """

    def __init__(self, max_entries: int = 500) -> None:
        self._max_entries = max_entries
        self._store: OrderedDict[tuple[str, int | None, int | None], FileStateEntry] = (
            OrderedDict()
        )

    def _key(
        self,
        path: str,
        offset: int | None,
        limit: int | None,
    ) -> tuple[str, int | None, int | None]:
        return (path, offset, limit)

    def get(
        self,
        path: str,
        offset: int | None,
        limit: int | None,
    ) -> FileStateEntry | None:
        """Return the cached entry if the file's mtime hasn't changed."""
        key = self._key(path, offset, limit)
        entry = self._store.get(key)
        if entry is None:
            return None

        # Validate mtime — if the file was modified, invalidate
        try:
            current_mtime = Path(path).stat().st_mtime
        except OSError:
            # File no longer exists — invalidate
            del self._store[key]
            return None

        if current_mtime != entry.mtime:
            del self._store[key]
            return None

        # Move to end (most recently used)
        self._store.move_to_end(key)
        return entry

    def put(
        self,
        path: str,
        content: str,
        mtime: float,
        offset: int | None,
        limit: int | None,
    ) -> None:
        """Insert or update a cache entry, evicting LRU if over capacity."""
        key = self._key(path, offset, limit)
        entry = FileStateEntry(
            content=content,
            mtime=mtime,
            offset=offset,
            limit=limit,
            size=len(content),
        )
        self._store[key] = entry
        self._store.move_to_end(key)

        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)

    def check_and_read(
        self,
        path: str,
        offset: int | None = None,
        limit: int | None = None,
    ) -> tuple[str | None, bool]:
        """Check cache. Returns (content, was_cached).

        If cached and fresh: returns (content, True).
        If not cached or stale: returns (None, False) — caller should read the file.
        """
        entry = self.get(path, offset, limit)
        if entry:
            return entry.content, True
        return None, False

    def clone(self) -> FileStateCache:
        """Return a shallow-independent copy of this cache."""
        new = FileStateCache(max_entries=self._max_entries)
        new._store = self._store.copy()
        return new
