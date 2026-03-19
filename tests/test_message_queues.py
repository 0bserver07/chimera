"""Tests for chimera.core.message_queue.MessageQueues (steering + follow-up)."""
from chimera.core.message_queue import MessageQueues
from chimera.types import Message


def test_empty_queues():
    q = MessageQueues()
    assert not q.has_steering
    assert not q.has_follow_up
    assert q.drain_steering() == []
    assert q.drain_follow_up() == []

def test_steer():
    q = MessageQueues()
    q.steer(Message.user("change direction"))
    assert q.has_steering
    msgs = q.drain_steering()
    assert len(msgs) == 1
    assert msgs[0].content == "change direction"
    assert not q.has_steering

def test_follow_up():
    q = MessageQueues()
    q.follow_up(Message.user("next task"))
    assert q.has_follow_up
    msgs = q.drain_follow_up()
    assert len(msgs) == 1
    assert msgs[0].content == "next task"
    assert not q.has_follow_up

def test_multiple_steering():
    q = MessageQueues()
    q.steer(Message.user("a"))
    q.steer(Message.user("b"))
    msgs = q.drain_steering()
    assert [m.content for m in msgs] == ["a", "b"]

def test_thread_safety():
    import threading
    q = MessageQueues()
    def _steer():
        q.steer(Message.user("from thread"))
    t = threading.Thread(target=_steer)
    t.start()
    t.join()
    assert q.has_steering
    assert q.drain_steering()[0].content == "from thread"
