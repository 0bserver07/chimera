"""Tests for chimera.analytics.manager — AnalyticsManager queueing and draining."""
from __future__ import annotations

import asyncio

import pytest

from chimera.analytics.manager import AnalyticsManager
from chimera.analytics.sinks import InMemorySink


class TestAnalyticsManager:

    def setup_method(self) -> None:
        self.manager = AnalyticsManager()

    def test_queues_before_sink(self) -> None:
        """Events are queued when no sink is attached."""
        self.manager.log_event("test_event", foo="bar")
        assert len(self.manager._queue) == 1
        assert self.manager._queue[0].name == "test_event"
        assert self.manager._queue[0].metadata == {"foo": "bar"}

    def test_strips_proto_keys(self) -> None:
        """Keys starting with _PROTO_ are stripped from metadata."""
        self.manager.log_event("evt", _PROTO_secret="hidden", visible="yes")
        assert "_PROTO_secret" not in self.manager._queue[0].metadata
        assert self.manager._queue[0].metadata == {"visible": "yes"}

    @pytest.mark.asyncio
    async def test_drain_on_attach(self) -> None:
        """Attaching a sink drains the queue into it."""
        self.manager.log_event("e1", x=1)
        self.manager.log_event("e2", x=2)
        sink = InMemorySink()
        self.manager.attach_sink(sink)
        # Give the drain task time to run
        await asyncio.sleep(0.05)
        assert len(sink.events) == 2
        assert sink.events[0].name == "e1"
        assert sink.events[1].name == "e2"
        assert len(self.manager._queue) == 0
