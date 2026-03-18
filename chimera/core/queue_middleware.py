"""Middleware that drains a :class:`MessageQueue` before each model call.

Injected messages appear as user messages in the context, allowing
external code to communicate with a running agent.

Usage::

    queue = MessageQueue()
    mw = MessageQueueMiddleware(queue)
    config = LoopConfig(middleware=[mw])

    # From another thread:
    queue.enqueue_text("Actually, use Python 3.12 not 3.11")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chimera.core.message_queue import MessageQueue

if TYPE_CHECKING:
    from chimera.core.context import Context
    from chimera.core.tool import BaseTool

# Try to import LoopMiddleware; if not available yet, define a stub
try:
    from chimera.core.middleware import LoopMiddleware
except ImportError:

    class LoopMiddleware:  # type: ignore[no-redef]
        """Minimal stub used when ``chimera.core.middleware`` is not yet available."""

        def before_model(self, context: Context, tools: list[BaseTool]) -> Context:  # type: ignore[override]
            return context

        def after_model(self, response: object, context: Context) -> object:  # type: ignore[override]
            return response

        def after_agent(self, result: object, env: object) -> object:  # type: ignore[override]
            return result


class MessageQueueMiddleware(LoopMiddleware):
    """Drain a :class:`MessageQueue` before each model call.

    Injected messages appear as user messages in the context,
    allowing external code to communicate with a running agent.

    Attributes:
        injected_count: Running total of messages injected across all
            ``before_model`` calls.
    """

    def __init__(self, queue: MessageQueue) -> None:
        self._queue = queue
        self.injected_count: int = 0

    def before_model(self, context: Context, tools: list[BaseTool]) -> Context:  # type: ignore[override]
        """Drain queue and add messages to context."""
        messages = self._queue.drain()
        for msg in messages:
            context.add(msg)
            self.injected_count += 1
        return context
