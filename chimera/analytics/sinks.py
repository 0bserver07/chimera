"""Built-in analytics sinks: file, stdout, and in-memory.

Provides :class:`FileSink`, :class:`StdoutSink`, and :class:`InMemorySink`
implementations of :class:`~chimera.analytics.manager.AnalyticsSink`.
"""
from __future__ import annotations

import json
from pathlib import Path

from chimera.analytics.manager import AnalyticsEvent, AnalyticsSink

__all__ = ["FileSink", "StdoutSink", "InMemorySink"]


class FileSink(AnalyticsSink):
    """Appends events as JSON lines to a file."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    async def log(self, event: AnalyticsEvent) -> None:
        """Append *event* as a single JSON line."""
        line = json.dumps({
            "name": event.name,
            "metadata": event.metadata,
            "timestamp": event.timestamp,
        })
        with self._path.open("a") as f:
            f.write(line + "\n")


class StdoutSink(AnalyticsSink):
    """Prints events to stdout."""

    async def log(self, event: AnalyticsEvent) -> None:
        """Print *event* to stdout."""
        print(f"[analytics] {event.name}: {event.metadata}")


class InMemorySink(AnalyticsSink):
    """Stores events in a list (for testing)."""

    def __init__(self) -> None:
        self.events: list[AnalyticsEvent] = []

    async def log(self, event: AnalyticsEvent) -> None:
        """Append *event* to the in-memory list."""
        self.events.append(event)
