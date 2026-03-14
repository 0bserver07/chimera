from __future__ import annotations

import queue
import uuid
from typing import TYPE_CHECKING, Any, Callable

from chimera.wire.types import WireMessage, WireRequest, WireResponse

if TYPE_CHECKING:
    from chimera.events.base import EventBus


class WireTimeout(Exception):
    """Raised when a wire request times out."""


class Wire:
    """Bidirectional communication channel between agent and UI.

    The agent side sends messages and requests. The UI side receives them
    via callbacks and can respond to requests.
    """

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._event_bus = event_bus
        self._response_queues: dict[str, queue.Queue[WireResponse]] = {}
        self._listeners: list[Callable[[WireMessage], None]] = []

    def on_message(self, callback: Callable[[WireMessage], None]) -> None:
        """Register a callback for all wire messages."""
        self._listeners.append(callback)

    def send(self, message: WireMessage) -> None:
        """Send a message from agent to UI (fire-and-forget)."""
        for listener in self._listeners:
            listener(message)
        if self._event_bus is not None:
            self._event_bus.publish(message)  # type: ignore[arg-type]

    def request(self, req: WireRequest) -> WireResponse:
        """Send a request and wait for a response (blocking).

        Raises:
            WireTimeout: If no response within req.timeout seconds.
        """
        if not req.request_id:
            req.request_id = uuid.uuid4().hex[:12]

        q: queue.Queue[WireResponse] = queue.Queue()
        self._response_queues[req.request_id] = q

        self.send(req)

        try:
            return q.get(timeout=req.timeout)
        except queue.Empty:
            raise WireTimeout(f"No response for request {req.request_id} within {req.timeout}s")
        finally:
            self._response_queues.pop(req.request_id, None)

    def respond(self, response: WireResponse) -> None:
        """Send a response from UI to agent."""
        q = self._response_queues.get(response.request_id)
        if q is not None:
            q.put(response)

    @property
    def pending_requests(self) -> int:
        """Number of pending (unanswered) requests."""
        return len(self._response_queues)
