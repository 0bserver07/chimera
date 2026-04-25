"""Tests for ProjectorRegistry transactional replay."""
from __future__ import annotations

from typing import Any

import pytest

from chimera.events.sourcing.projector import (
    Projector,
    ProjectorError,
    ProjectorRegistry,
)


class _Counter(Projector):
    name = "counter"

    def __init__(self) -> None:
        self.applied: list[tuple[str, Any]] = []

    def apply(self, event_name: str, payload: Any) -> None:
        self.applied.append((event_name, payload))

    def reset(self) -> None:
        self.applied.clear()


class _Failing(Projector):
    name = "failing"

    def __init__(self, fail_at_seq: int) -> None:
        self._fail_at = fail_at_seq
        self.applied: list[Any] = []

    def apply(self, event_name: str, payload: Any) -> None:
        seq = getattr(payload, "seq", None) or payload.get("seq")
        if seq == self._fail_at:
            raise RuntimeError(f"boom at {seq}")
        self.applied.append(payload)


def test_register_duplicate_raises() -> None:
    reg = ProjectorRegistry()
    reg.register(_Counter())
    with pytest.raises(ValueError):
        reg.register(_Counter())


def test_register_blank_name_raises() -> None:
    class Bad(Projector):
        name = ""

        def apply(self, event_name: str, payload: Any) -> None:
            pass

    reg = ProjectorRegistry()
    with pytest.raises(ValueError):
        reg.register(Bad())


def test_replay_advances_cursor() -> None:
    reg = ProjectorRegistry()
    counter = _Counter()
    reg.register(counter)
    events = [(1, "a", {"x": 1}), (2, "b", {"x": 2})]
    n = reg.replay(events)
    assert n == 2
    assert reg.cursor_for("counter") == 2
    assert counter.applied == [("a", {"x": 1}), ("b", {"x": 2})]


def test_replay_idempotent_skip_at_or_below_cursor() -> None:
    reg = ProjectorRegistry()
    counter = _Counter()
    reg.register(counter)
    reg.set_cursor("counter", 2)
    events = [(1, "old", {}), (2, "old", {}), (3, "new", {})]
    n = reg.replay(events)
    assert n == 3  # all events traversed
    # but only seq=3 was actually applied
    assert counter.applied == [("new", {})]
    assert reg.cursor_for("counter") == 3


def test_replay_rolls_back_on_error() -> None:
    reg = ProjectorRegistry()
    counter = _Counter()
    failing = _Failing(fail_at_seq=2)
    reg.register(counter)
    reg.register(failing)

    events = [
        (1, "ok", {"seq": 1}),
        (2, "bad", {"seq": 2}),
    ]
    with pytest.raises(ProjectorError):
        reg.replay(events)

    # Counter applied seq=1 successfully but rolled back when failing
    # tried seq=2, so its cursor returns to 1.
    assert reg.cursor_for("counter") == 1
    # Re-running with the same seq should retry from cursor+1.
    # First fix the failing projector by replacing it, then replay seq=2.
    reg2 = ProjectorRegistry()
    reg2.register(_Counter())
    reg2.set_cursor("counter", 1)
    reg2.replay([(2, "ok", {"seq": 2})])
    assert reg2.cursor_for("counter") == 2


def test_reset_rewinds_cursors() -> None:
    reg = ProjectorRegistry()
    counter = _Counter()
    reg.register(counter)
    reg.replay([(1, "a", {})])
    reg.reset()
    assert reg.cursor_for("counter") == 0
    assert counter.applied == []
