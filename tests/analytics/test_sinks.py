"""Tests for chimera.analytics.sinks — InMemorySink and FileSink."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.analytics.manager import AnalyticsEvent
from chimera.analytics.sinks import FileSink, InMemorySink


class TestInMemorySink:

    @pytest.mark.asyncio
    async def test_stores_events(self) -> None:
        """InMemorySink stores events in its .events list."""
        sink = InMemorySink()
        event = AnalyticsEvent(name="test", metadata={"k": "v"}, timestamp=1.0)
        await sink.log(event)
        assert len(sink.events) == 1
        assert sink.events[0].name == "test"


class TestFileSink:

    @pytest.mark.asyncio
    async def test_writes_jsonl(self, tmp_path: Path) -> None:
        """FileSink writes one JSON line per event."""
        path = tmp_path / "events.jsonl"
        sink = FileSink(str(path))
        await sink.log(AnalyticsEvent(name="a", metadata={"x": 1}, timestamp=100.0))
        await sink.log(AnalyticsEvent(name="b", metadata={}, timestamp=200.0))

        lines = path.read_text().strip().splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["name"] == "a"
        assert first["metadata"] == {"x": 1}
        assert first["timestamp"] == 100.0
