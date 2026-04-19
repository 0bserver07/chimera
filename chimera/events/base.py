# chimera/events/base.py
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from chimera.events.middleware import Middleware

__all__ = ["Event", "EventHandler", "EventBus"]


@dataclass
class Event:
    """Base event emitted by the Chimera runtime."""

    type: str
    timestamp: float = field(default_factory=time.monotonic)
    metadata: dict[str, Any] = field(default_factory=dict)


EventHandler = Callable[[Event], None]


class EventBus:
    """Publish / subscribe event bus with wildcard and middleware support."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}
        self._middlewares: list[Middleware] = []

    # -- subscription --------------------------------------------------------

    def subscribe(self, event_type: str, handler: EventHandler) -> Callable[[], None]:
        """Register *handler* for *event_type* and return an unsubscribe callable."""
        self._handlers.setdefault(event_type, []).append(handler)

        def _unsubscribe() -> None:
            handlers = self._handlers.get(event_type)
            if handlers and handler in handlers:
                handlers.remove(handler)

        return _unsubscribe

    def on(self, event_type: str) -> Callable[[EventHandler], EventHandler]:
        """Decorator form of :meth:`subscribe`."""

        def _decorator(handler: EventHandler) -> EventHandler:
            self.subscribe(event_type, handler)
            return handler

        return _decorator

    # -- publishing ----------------------------------------------------------

    def publish(self, event: Event) -> None:
        """Publish *event* to all matching handlers (exact type + wildcard ``"*"``)."""

        def _dispatch(evt: Event) -> None:
            for handler in self._handlers.get(evt.type, []):
                handler(evt)
            for handler in self._handlers.get("*", []):
                handler(evt)

        if not self._middlewares:
            _dispatch(event)
            return

        # Build middleware chain (outermost wraps innermost).
        chain: Callable[[Event], None] = _dispatch
        for mw in reversed(self._middlewares):
            # Capture *mw* and *chain* in the closure via default args.
            def _wrap(e: Event, _mw: Middleware = mw, _next: Callable[[Event], None] = chain) -> None:
                _mw.process(e, _next)
            chain = _wrap

        chain(event)

    # -- middleware -----------------------------------------------------------

    def use(self, middleware: Middleware) -> None:
        """Append *middleware* to the processing chain."""
        self._middlewares.append(middleware)

    # -- housekeeping --------------------------------------------------------

    def clear(self) -> None:
        """Remove all handlers and middleware."""
        self._handlers.clear()
        self._middlewares.clear()
