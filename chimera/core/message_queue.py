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


from dataclasses import dataclass, field  # noqa: E402


@dataclass
class MessageQueues:
    """Thread-safe steering, follow-up, and next-turn message queues.

    Provides three separate queues:

    - **steering** — injected mid-turn to redirect the running agent.
    - **follow_up** — queued for after the current turn completes.
    - **next_turn** — like follow-up, but **survives cancellation**: steering
      and follow-up are conversational state for the *current* run and are
      cleared with it, while next-turn messages are delivered at the start of
      whatever run comes next (even after an abort). Use it for "when you get
      a chance, also…" instructions that must not die with a cancelled turn.

    All queues are thread-safe; any thread can call :meth:`steer`,
    :meth:`follow_up`, or :meth:`next_turn` while the loop is running.
    """

    _steering: deque[Message] = field(default_factory=deque)
    _follow_up: deque[Message] = field(default_factory=deque)
    _next_turn: deque[Message] = field(default_factory=deque)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def steer(self, message: Message) -> None:
        """Enqueue a steering message. Thread-safe."""
        with self._lock:
            self._steering.append(message)

    def follow_up(self, message: Message) -> None:
        """Enqueue a follow-up message. Thread-safe."""
        with self._lock:
            self._follow_up.append(message)

    def drain_steering(self) -> list[Message]:
        """Remove and return all steering messages. Thread-safe."""
        with self._lock:
            msgs = list(self._steering)
            self._steering.clear()
            return msgs

    def drain_follow_up(self) -> list[Message]:
        """Remove and return all follow-up messages. Thread-safe."""
        with self._lock:
            msgs = list(self._follow_up)
            self._follow_up.clear()
            return msgs

    def next_turn(self, message: Message) -> None:
        """Enqueue a next-turn message (survives cancellation). Thread-safe."""
        with self._lock:
            self._next_turn.append(message)

    def drain_next_turn(self) -> list[Message]:
        """Remove and return all next-turn messages. Thread-safe."""
        with self._lock:
            msgs = list(self._next_turn)
            self._next_turn.clear()
            return msgs

    def clear_run_state(self) -> None:
        """Drop steering + follow-up (a cancelled run's state) — keep next-turn.

        Call on abort: the two run-scoped queues die with the run, while
        next-turn messages persist for the next run by design.
        """
        with self._lock:
            self._steering.clear()
            self._follow_up.clear()

    @property
    def has_steering(self) -> bool:
        """Return ``True`` if there are steering messages pending."""
        with self._lock:
            return len(self._steering) > 0

    @property
    def has_follow_up(self) -> bool:
        """Return ``True`` if there are follow-up messages pending."""
        with self._lock:
            return len(self._follow_up) > 0

    @property
    def has_next_turn(self) -> bool:
        """Return ``True`` if there are next-turn messages pending."""
        with self._lock:
            return len(self._next_turn) > 0


class SteeringMessageQueue:
    """Queue for injecting messages into the agent loop mid-run.

    Provides two channels:

    - **steering** -- injected between tool turns (mid-run).
    - **follow_up** -- injected when the agent would otherwise stop.
    """

    def __init__(self) -> None:
        self._steering: deque[Message] = deque()
        self._follow_up: deque[Message] = deque()

    def add_steering(self, message: Message) -> None:
        """Add a message to be injected between tool turns (mid-run)."""
        self._steering.append(message)

    def add_follow_up(self, message: Message) -> None:
        """Add a message to be injected when the agent would stop."""
        self._follow_up.append(message)

    def drain_steering(self) -> list[Message]:
        """Drain all pending steering messages."""
        msgs = list(self._steering)
        self._steering.clear()
        return msgs

    def drain_follow_up(self) -> list[Message]:
        """Drain all pending follow-up messages."""
        msgs = list(self._follow_up)
        self._follow_up.clear()
        return msgs

    def has_steering(self) -> bool:
        return len(self._steering) > 0

    def has_follow_up(self) -> bool:
        return len(self._follow_up) > 0

    def clear(self) -> None:
        self._steering.clear()
        self._follow_up.clear()
