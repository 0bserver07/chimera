"""File watcher: detect external file changes for reactive agent re-runs.

Watches the working directory for changes made outside the agent (e.g. user
edits in their IDE). Emits events via EventBus and can trigger callbacks.

Uses polling as the cross-platform default. OS-native watchers (FSEvents,
inotify) can be added as optional backends.
"""
from __future__ import annotations

import fnmatch
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from chimera.events.base import EventBus


class ChangeType(Enum):
    """Type of file change detected."""
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass
class FileChange:
    """A detected file change."""
    path: str
    change_type: ChangeType
    timestamp: float = field(default_factory=time.time)


class FileWatcher:
    """Watch a directory for file changes.

    Example::

        watcher = FileWatcher("/path/to/project", patterns=["*.py"])
        watcher.on_change(lambda changes: print(changes))
        watcher.start()
        # ... later ...
        watcher.stop()
    """

    def __init__(
        self,
        workdir: str,
        patterns: list[str] | None = None,
        ignore: list[str] | None = None,
        poll_interval: float = 1.0,
        debounce_ms: int = 100,
    ) -> None:
        self._workdir = Path(workdir)
        self._patterns = patterns or ["*"]
        self._ignore = ignore or [
            "__pycache__/*", ".git/*", "*.pyc", ".venv/*",
            "node_modules/*", ".mypy_cache/*",
        ]
        self._poll_interval = poll_interval
        self._debounce_ms = debounce_ms
        self._callbacks: list[Callable[[list[FileChange]], None]] = []
        self._snapshots: dict[str, float] = {}
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def on_change(self, callback: Callable[[list[FileChange]], None]) -> None:
        """Register a callback for file changes."""
        self._callbacks.append(callback)

    def start(self) -> None:
        """Start watching in a background thread."""
        self._snapshots = self._scan()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop watching."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    @property
    def is_running(self) -> bool:
        """Whether the watcher is active."""
        return self._thread is not None and self._thread.is_alive()

    def _poll_loop(self) -> None:
        """Polling loop that detects changes."""
        while not self._stop_event.is_set():
            self._stop_event.wait(self._poll_interval)
            if self._stop_event.is_set():
                break

            new_snapshot = self._scan()
            changes = self._diff(self._snapshots, new_snapshot)

            if changes:
                # Debounce: wait a bit then re-scan to batch rapid changes
                time.sleep(self._debounce_ms / 1000)
                new_snapshot = self._scan()
                changes = self._diff(self._snapshots, new_snapshot)

                if changes:
                    self._snapshots = new_snapshot
                    for cb in self._callbacks:
                        try:
                            cb(changes)
                        except Exception:
                            pass

    def _scan(self) -> dict[str, float]:
        """Scan the directory and return {path: mtime} dict."""
        result: dict[str, float] = {}
        for root, dirs, files in os.walk(self._workdir):
            # Skip ignored directories
            dirs[:] = [
                d for d in dirs
                if not any(fnmatch.fnmatch(d, pat.rstrip("/*")) for pat in self._ignore if pat.endswith("/*"))
            ]
            for fname in files:
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, self._workdir)

                # Check ignore patterns
                if any(fnmatch.fnmatch(rel, pat) for pat in self._ignore):
                    continue

                # Check include patterns
                if not any(fnmatch.fnmatch(fname, pat) for pat in self._patterns):
                    continue

                try:
                    result[rel] = os.path.getmtime(full)
                except OSError:
                    pass

        return result

    def _diff(
        self,
        old: dict[str, float],
        new: dict[str, float],
    ) -> list[FileChange]:
        """Compare two snapshots and return changes."""
        changes: list[FileChange] = []

        for path, mtime in new.items():
            if path not in old:
                changes.append(FileChange(path, ChangeType.CREATED))
            elif mtime != old[path]:
                changes.append(FileChange(path, ChangeType.MODIFIED))

        for path in old:
            if path not in new:
                changes.append(FileChange(path, ChangeType.DELETED))

        return changes

    def check_once(self) -> list[FileChange]:
        """Do a single check (no background thread). Useful for testing."""
        new_snapshot = self._scan()
        changes = self._diff(self._snapshots, new_snapshot)
        self._snapshots = new_snapshot
        return changes


def connect_watcher_to_event_bus(
    watcher: FileWatcher,
    event_bus: "EventBus",
) -> None:
    """Bridge file watcher events to an EventBus.

    Registers a callback on *watcher* that publishes an
    :class:`~chimera.events.base.Event` with ``type="file_change"`` for
    each :class:`FileChange` detected.

    Args:
        watcher: The FileWatcher instance to connect.
        event_bus: The EventBus to publish events to.

    Example::

        from chimera.env.watcher import FileWatcher, connect_watcher_to_event_bus
        from chimera.events.base import EventBus

        bus = EventBus()
        watcher = FileWatcher("/path/to/project", patterns=["*.py"])
        connect_watcher_to_event_bus(watcher, bus)
        watcher.start()
    """
    from chimera.events.base import Event

    def _on_changes(changes: list[FileChange]) -> None:
        for change in changes:
            event_bus.publish(
                Event(
                    type="file_change",
                    metadata={
                        "path": change.path,
                        "change_type": change.change_type.value,
                        "timestamp": change.timestamp,
                    },
                )
            )

    watcher.on_change(_on_changes)
