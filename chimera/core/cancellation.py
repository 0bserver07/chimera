"""Cooperative cancellation token for agent operations."""
from __future__ import annotations

import threading
from typing import Callable


class OperationCancelled(Exception):
    """Raised when a cancellation token is checked after cancel()."""
    pass


class CancellationToken:
    """Cooperative cancellation token. Thread-safe."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    def cancel(self) -> None:
        self._cancelled.set()
        with self._lock:
            for cb in self._callbacks:
                cb()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check(self) -> None:
        if self._cancelled.is_set():
            raise OperationCancelled("Operation cancelled")

    def on_cancel(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if self._cancelled.is_set():
                callback()
            else:
                self._callbacks.append(callback)

    def wait(self, timeout: float | None = None) -> bool:
        return self._cancelled.wait(timeout)

    def threading_event(self) -> threading.Event:
        """Return the underlying :class:`threading.Event`.

        Useful for plumbing the token through APIs (notably HTTP / provider
        layers) that take a generic ``threading.Event`` as their cancel
        primitive. The returned event is the same one ``cancel()`` sets, so
        a watcher waiting on it observes the cancellation immediately.
        """
        return self._cancelled


class CancellableTool:
    """Mixin for tools that support cooperative cancellation."""

    _cancel_token: CancellationToken | None = None

    def bind_cancellation(self, token: CancellationToken) -> None:
        self._cancel_token = token
