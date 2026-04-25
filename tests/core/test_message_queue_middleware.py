"""Tests for MessageQueue and MessageQueueMiddleware."""

from __future__ import annotations

import threading

from chimera.core.message_queue import MessageQueue
from chimera.core.queue_middleware import MessageQueueMiddleware
from chimera.core.context import Context
from chimera.types import Message


def test_enqueue_and_drain():
    q = MessageQueue()
    q.enqueue(Message.user("hello"))
    q.enqueue(Message.user("world"))
    msgs = q.drain()
    assert len(msgs) == 2
    assert msgs[0].content == "hello"
    assert q.is_empty()


def test_enqueue_text():
    q = MessageQueue()
    q.enqueue_text("hello")
    msgs = q.drain()
    assert msgs[0].role == "user"
    assert msgs[0].content == "hello"


def test_drain_empty():
    q = MessageQueue()
    assert q.drain() == []


def test_peek():
    q = MessageQueue()
    q.enqueue_text("hello")
    peeked = q.peek()
    assert len(peeked) == 1
    assert not q.is_empty()  # peek doesn't remove


def test_size():
    q = MessageQueue()
    assert q.size == 0
    q.enqueue_text("a")
    q.enqueue_text("b")
    assert q.size == 2


def test_clear():
    q = MessageQueue()
    q.enqueue_text("a")
    q.clear()
    assert q.is_empty()


def test_thread_safety():
    q = MessageQueue()

    def producer():
        for i in range(100):
            q.enqueue_text(f"msg-{i}")

    threads = [threading.Thread(target=producer) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    msgs = q.drain()
    assert len(msgs) == 500


def test_middleware_injects_messages():
    q = MessageQueue()
    q.enqueue_text("follow-up 1")
    q.enqueue_text("follow-up 2")

    mw = MessageQueueMiddleware(q)
    ctx = Context(system="test")
    ctx.add(Message.user("original task"))

    mw.before_model(ctx, [])

    assert len(ctx.messages) == 3
    assert ctx.messages[1].content == "follow-up 1"
    assert ctx.messages[2].content == "follow-up 2"
    assert mw.injected_count == 2


def test_middleware_empty_queue():
    q = MessageQueue()
    mw = MessageQueueMiddleware(q)
    ctx = Context()
    result = mw.before_model(ctx, [])
    assert result is ctx
    assert mw.injected_count == 0


def test_middleware_multiple_drains():
    q = MessageQueue()
    mw = MessageQueueMiddleware(q)
    ctx = Context()

    q.enqueue_text("batch1")
    mw.before_model(ctx, [])
    assert mw.injected_count == 1

    q.enqueue_text("batch2")
    mw.before_model(ctx, [])
    assert mw.injected_count == 2
