"""Analytics event manager with pluggable sinks.

Provides :class:`AnalyticsEvent`, :class:`AnalyticsSink` (ABC), and
:class:`AnalyticsManager` which queues events until a sink is attached,
then drains the queue and sends future events directly.
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

__all__ = ["AnalyticsEvent", "AnalyticsSink", "AnalyticsManager"]


@dataclass
class AnalyticsEvent:
    """A single analytics event."""

    name: str
    metadata: dict[str, Any]
    timestamp: float = field(default_factory=time.time)


class AnalyticsSink(ABC):
    """Abstract base class for analytics sinks."""

    @abstractmethod
    async def log(self, event: AnalyticsEvent) -> None:
        """Persist or forward *event*."""


class AnalyticsManager:
    """Manages analytics event collection and delivery.

    Events logged before a sink is attached are queued (up to
    *_max_queue*).  When :meth:`attach_sink` is called, queued events are
    drained asynchronously.
    """

    def __init__(self) -> None:
        self._sink: AnalyticsSink | None = None
        self._queue: list[AnalyticsEvent] = []
        self._max_queue: int = 100

    def attach_sink(self, sink: AnalyticsSink) -> None:
        """Set the active sink and drain the queue via ``asyncio.create_task``."""
        self._sink = sink
        if self._queue:
            asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        """Send all queued events to the attached sink."""
        while self._queue:
            event = self._queue.pop(0)
            if self._sink is not None:
                await self._sink.log(event)

    def log_event(self, name: str, **metadata: Any) -> None:
        """Log an event synchronously.

        Keys starting with ``_PROTO_`` are stripped from *metadata*.
        If a sink is attached the event is sent via ``asyncio.create_task``;
        otherwise it is queued.
        """
        cleaned = {k: v for k, v in metadata.items() if not k.startswith("_PROTO_")}
        event = AnalyticsEvent(name=name, metadata=cleaned)
        if self._sink is not None:
            asyncio.create_task(self._sink.log(event))
        else:
            if len(self._queue) < self._max_queue:
                self._queue.append(event)

    async def log_event_async(self, name: str, **metadata: Any) -> None:
        """Log an event asynchronously.

        Keys starting with ``_PROTO_`` are stripped from *metadata*.
        """
        cleaned = {k: v for k, v in metadata.items() if not k.startswith("_PROTO_")}
        event = AnalyticsEvent(name=name, metadata=cleaned)
        if self._sink is not None:
            await self._sink.log(event)
        else:
            if len(self._queue) < self._max_queue:
                self._queue.append(event)
