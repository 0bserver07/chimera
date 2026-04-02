from __future__ import annotations
import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Awaitable


class EventType(Enum):
    IMMEDIATE = "immediate"    # Fire now
    ONE_SHOT = "one_shot"      # Fire at specific time
    PERIODIC = "periodic"      # Fire on schedule (cron-like)


@dataclass
class ScheduledEvent:
    event_id: str
    event_type: EventType
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    fire_at: float = 0.0          # Unix timestamp for one_shot
    interval_seconds: float = 0.0  # For periodic
    last_fired: float = 0.0
    created_at: float = field(default_factory=time.time)


class AgentScheduler:
    """Schedule and execute agent-initiated events."""

    def __init__(self, handler: Callable[[ScheduledEvent], Awaitable[None]] | None = None):
        self._events: dict[str, ScheduledEvent] = {}
        self._handler = handler
        self._running = False
        self._task: asyncio.Task[None] | None = None

    def schedule_immediate(self, event_id: str, name: str, payload: dict[str, Any] | None = None) -> ScheduledEvent:
        """Schedule an event to fire immediately on next check."""
        event = ScheduledEvent(
            event_id=event_id, event_type=EventType.IMMEDIATE,
            name=name, payload=payload or {},
        )
        self._events[event_id] = event
        return event

    def schedule_one_shot(self, event_id: str, name: str, fire_at: float, payload: dict[str, Any] | None = None) -> ScheduledEvent:
        """Schedule an event to fire once at a specific time."""
        event = ScheduledEvent(
            event_id=event_id, event_type=EventType.ONE_SHOT,
            name=name, fire_at=fire_at, payload=payload or {},
        )
        self._events[event_id] = event
        return event

    def schedule_periodic(self, event_id: str, name: str, interval_seconds: float, payload: dict[str, Any] | None = None) -> ScheduledEvent:
        """Schedule a recurring event."""
        event = ScheduledEvent(
            event_id=event_id, event_type=EventType.PERIODIC,
            name=name, interval_seconds=interval_seconds, payload=payload or {},
        )
        self._events[event_id] = event
        return event

    def cancel(self, event_id: str) -> bool:
        return self._events.pop(event_id, None) is not None

    def list_events(self) -> list[ScheduledEvent]:
        return list(self._events.values())

    async def check_and_fire(self) -> list[ScheduledEvent]:
        """Check all events and fire any that are due. Returns fired events."""
        now = time.time()
        fired: list[ScheduledEvent] = []
        to_remove: list[str] = []

        for event_id, event in self._events.items():
            should_fire = False

            if event.event_type == EventType.IMMEDIATE:
                should_fire = True
                to_remove.append(event_id)

            elif event.event_type == EventType.ONE_SHOT:
                if now >= event.fire_at:
                    should_fire = True
                    to_remove.append(event_id)

            elif event.event_type == EventType.PERIODIC:
                if event.last_fired == 0 or (now - event.last_fired) >= event.interval_seconds:
                    should_fire = True
                    event.last_fired = now

            if should_fire:
                fired.append(event)
                if self._handler:
                    try:
                        await self._handler(event)
                    except Exception:
                        pass

        for eid in to_remove:
            self._events.pop(eid, None)

        return fired

    async def run_loop(self, check_interval: float = 1.0) -> None:
        """Run the scheduler loop. Checks events every check_interval seconds."""
        self._running = True
        while self._running:
            await self.check_and_fire()
            await asyncio.sleep(check_interval)

    def stop(self) -> None:
        self._running = False

    def save(self, path: Path) -> None:
        """Persist scheduled events to disk."""
        data: dict[str, Any] = {}
        for eid, event in self._events.items():
            data[eid] = {
                "event_type": event.event_type.value,
                "name": event.name,
                "payload": event.payload,
                "fire_at": event.fire_at,
                "interval_seconds": event.interval_seconds,
                "last_fired": event.last_fired,
                "created_at": event.created_at,
            }
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path, handler: Callable[[ScheduledEvent], Awaitable[None]] | None = None) -> AgentScheduler:
        """Load scheduled events from disk."""
        scheduler = cls(handler=handler)
        if path.exists():
            data = json.loads(path.read_text())
            for eid, info in data.items():
                event = ScheduledEvent(
                    event_id=eid,
                    event_type=EventType(info["event_type"]),
                    name=info["name"],
                    payload=info.get("payload", {}),
                    fire_at=info.get("fire_at", 0),
                    interval_seconds=info.get("interval_seconds", 0),
                    last_fired=info.get("last_fired", 0),
                    created_at=info.get("created_at", 0),
                )
                scheduler._events[eid] = event
        return scheduler
