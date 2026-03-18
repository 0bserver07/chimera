"""Thread-safe message queue for injecting messages into a running agent.

External code (webhooks, REPL, other threads) can enqueue messages
while the agent loop is running.  A middleware drains the queue
before each model call, injecting the messages into context.
"""

from __future__ import annotations

import threading
from collections import deque

from chimera.types import Message


class MessageQueue:
    """Thread-safe queue for injecting messages into a running agent.

    External code (webhooks, REPL, other threads) can enqueue messages
    while the agent loop is running. A middleware drains the queue
    before each model call, injecting the messages into context.

    Thread-safe: enqueue() can be called from any thread.
    """

    def __init__(self) -> None:
        self._queue: deque[Message] = deque()
        self._lock = threading.Lock()

    def enqueue(self, message: Message) -> None:
        """Add a message to the queue. Thread-safe."""
        with self._lock:
            self._queue.append(message)

    def enqueue_text(self, text: str) -> None:
        """Convenience: enqueue a user message from text."""
        self.enqueue(Message.user(text))

    def drain(self) -> list[Message]:
        """Remove and return all queued messages. Thread-safe."""
        with self._lock:
            messages = list(self._queue)
            self._queue.clear()
            return messages

    def peek(self) -> list[Message]:
        """View queued messages without removing them."""
        with self._lock:
            return list(self._queue)

    def is_empty(self) -> bool:
        """Check if the queue has messages."""
        with self._lock:
            return len(self._queue) == 0

    @property
    def size(self) -> int:
        """Return the number of queued messages."""
        with self._lock:
            return len(self._queue)

    def clear(self) -> None:
        """Remove all queued messages."""
        with self._lock:
            self._queue.clear()
