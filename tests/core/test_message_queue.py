"""Tests for SteeringMessageQueue (steering + follow-up injection)."""
from __future__ import annotations

from chimera.core.message_queue import SteeringMessageQueue
from chimera.types import Message


def test_add_and_drain_steering():
    q = SteeringMessageQueue()
    q.add_steering(Message.user("steer 1"))
    q.add_steering(Message.user("steer 2"))
    msgs = q.drain_steering()
    assert len(msgs) == 2
    assert msgs[0].content == "steer 1"
    assert msgs[1].content == "steer 2"


def test_add_and_drain_follow_up():
    q = SteeringMessageQueue()
    q.add_follow_up(Message.user("follow 1"))
    q.add_follow_up(Message.user("follow 2"))
    msgs = q.drain_follow_up()
    assert len(msgs) == 2
    assert msgs[0].content == "follow 1"
    assert msgs[1].content == "follow 2"


def test_drain_clears_queue():
    q = SteeringMessageQueue()
    q.add_steering(Message.user("a"))
    q.add_follow_up(Message.user("b"))
    q.drain_steering()
    q.drain_follow_up()
    assert not q.has_steering()
    assert not q.has_follow_up()
    assert q.drain_steering() == []
    assert q.drain_follow_up() == []


def test_has_steering_and_follow_up():
    q = SteeringMessageQueue()
    assert not q.has_steering()
    assert not q.has_follow_up()
    q.add_steering(Message.user("s"))
    assert q.has_steering()
    assert not q.has_follow_up()
    q.add_follow_up(Message.user("f"))
    assert q.has_follow_up()


def test_clear():
    q = SteeringMessageQueue()
    q.add_steering(Message.user("s"))
    q.add_follow_up(Message.user("f"))
    q.clear()
    assert not q.has_steering()
    assert not q.has_follow_up()
