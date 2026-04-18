"""Tests for chimera.core.scheduler — Scheduled Agent Events."""
from __future__ import annotations

import time
import pytest
from pathlib import Path

from chimera.core.scheduler import AgentScheduler, ScheduledEvent, EventType


@pytest.fixture
def scheduler() -> AgentScheduler:
    return AgentScheduler()


# ---------- test_schedule_immediate_fires ----------

@pytest.mark.asyncio
async def test_schedule_immediate_fires(scheduler: AgentScheduler) -> None:
    scheduler.schedule_immediate("e1", "ping", payload={"msg": "hello"})
    fired = await scheduler.check_and_fire()
    assert len(fired) == 1
    assert fired[0].event_id == "e1"
    assert fired[0].name == "ping"
    assert fired[0].payload == {"msg": "hello"}
    # Immediate events are removed after firing
    assert scheduler.list_events() == []


# ---------- test_schedule_one_shot_fires_at_time ----------

@pytest.mark.asyncio
async def test_schedule_one_shot_fires_at_time(scheduler: AgentScheduler) -> None:
    past = time.time() - 10  # already in the past
    scheduler.schedule_one_shot("e2", "reminder", fire_at=past)
    fired = await scheduler.check_and_fire()
    assert len(fired) == 1
    assert fired[0].event_id == "e2"
    # Should be removed after firing
    assert scheduler.list_events() == []


# ---------- test_schedule_one_shot_not_before_time ----------

@pytest.mark.asyncio
async def test_schedule_one_shot_not_before_time(scheduler: AgentScheduler) -> None:
    future = time.time() + 3600  # 1 hour from now
    scheduler.schedule_one_shot("e3", "future_event", fire_at=future)
    fired = await scheduler.check_and_fire()
    assert len(fired) == 0
    # Event should still be present
    assert len(scheduler.list_events()) == 1


# ---------- test_schedule_periodic_fires_repeatedly ----------

@pytest.mark.asyncio
async def test_schedule_periodic_fires_repeatedly(scheduler: AgentScheduler) -> None:
    scheduler.schedule_periodic("e4", "heartbeat", interval_seconds=0.0)
    # First check — should fire (last_fired == 0)
    fired1 = await scheduler.check_and_fire()
    assert len(fired1) == 1
    # Event should still exist (periodic is not removed)
    assert len(scheduler.list_events()) == 1
    # Second check — interval is 0 so it should fire again immediately
    fired2 = await scheduler.check_and_fire()
    assert len(fired2) == 1


# ---------- test_cancel_event ----------

@pytest.mark.asyncio
async def test_cancel_event(scheduler: AgentScheduler) -> None:
    scheduler.schedule_immediate("e5", "will_cancel")
    assert scheduler.cancel("e5") is True
    assert scheduler.cancel("e5") is False
    fired = await scheduler.check_and_fire()
    assert len(fired) == 0


# ---------- test_list_events ----------

def test_list_events(scheduler: AgentScheduler) -> None:
    scheduler.schedule_immediate("a", "alpha")
    scheduler.schedule_one_shot("b", "beta", fire_at=time.time() + 100)
    events = scheduler.list_events()
    assert len(events) == 2
    ids = {e.event_id for e in events}
    assert ids == {"a", "b"}


# ---------- test_save_and_load ----------

def test_save_and_load(tmp_path: Path) -> None:
    path = tmp_path / "events.json"
    s1 = AgentScheduler()
    s1.schedule_one_shot("x", "backup", fire_at=1234567890.0, payload={"db": "main"})
    s1.schedule_periodic("y", "health", interval_seconds=60.0)
    s1.save(path)

    s2 = AgentScheduler.load(path)
    events = {e.event_id: e for e in s2.list_events()}
    assert "x" in events
    assert events["x"].name == "backup"
    assert events["x"].fire_at == 1234567890.0
    assert events["x"].payload == {"db": "main"}
    assert events["x"].event_type == EventType.ONE_SHOT

    assert "y" in events
    assert events["y"].interval_seconds == 60.0
    assert events["y"].event_type == EventType.PERIODIC


# ---------- test_handler_called_on_fire ----------

@pytest.mark.asyncio
async def test_handler_called_on_fire() -> None:
    received: list[ScheduledEvent] = []

    async def handler(event: ScheduledEvent) -> None:
        received.append(event)

    scheduler = AgentScheduler(handler=handler)
    scheduler.schedule_immediate("h1", "notify", payload={"x": 1})
    await scheduler.check_and_fire()

    assert len(received) == 1
    assert received[0].event_id == "h1"
    assert received[0].payload == {"x": 1}
