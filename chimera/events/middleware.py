# chimera/events/middleware.py
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from chimera.events.base import Event

__all__ = ["Middleware", "LoggingMiddleware", "FilterMiddleware"]

logger = logging.getLogger(__name__)


class Middleware(ABC):
    """Abstract base for event middleware."""

    @abstractmethod
    def process(self, event: Event, next_handler: Callable[[Event], None]) -> None:
        """Process *event* and optionally call *next_handler* to continue the chain."""


class LoggingMiddleware(Middleware):
    """Logs every event's type and timestamp before forwarding."""

    def __init__(self, log: logging.Logger | None = None) -> None:
        self._log = log or logger

    def process(self, event: Event, next_handler: Callable[[Event], None]) -> None:
        self._log.debug("Event %s at %s", event.type, event.timestamp)
        next_handler(event)


class FilterMiddleware(Middleware):
    """Only forwards events whose type is in *allow_types*."""

    def __init__(self, allow_types: set[str]) -> None:
        self._allow_types = allow_types

    def process(self, event: Event, next_handler: Callable[[Event], None]) -> None:
        if event.type in self._allow_types:
            next_handler(event)
