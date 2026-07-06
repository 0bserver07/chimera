from __future__ import annotations

import asyncio
import threading
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from chimera.types import Message, ToolCall

if TYPE_CHECKING:
    from chimera.providers.thinking import ThinkingLevel


@dataclass
class Response:
    content: str
    tool_calls: list[ToolCall]
    usage: dict[str, int]

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


@dataclass
class StreamEvent:
    """A single event from a streaming LLM response.

    Args:
        type: One of ``"text_delta"``, ``"tool_call_start"``,
            ``"tool_call_delta"``, ``"tool_call_complete"``, ``"done"``.
        content: Text content for ``text_delta`` events.
        tool_call: Associated :class:`ToolCall` (partial or complete).
        usage: Token usage dict, typically set on ``done`` events.
    """

    type: str
    content: str = ""
    tool_call: ToolCall | None = None
    usage: dict[str, int] | None = None


ToolSchema = dict[str, Any]


#: Valid values for the provider-agnostic ``cache`` knob (see :class:`Provider`).
CACHE_LEVELS = ("none", "short", "long")


class Provider(ABC):
    """LLM backend. Any class implementing complete() works.

    Prompt caching (``cache`` convention)
    -------------------------------------
    Providers whose backend supports prompt caching honor a ctor-level
    ``cache`` string with three values (see :data:`CACHE_LEVELS`):

    * ``"none"`` — no caching. **The default; zero behavior change.**
    * ``"short"`` — 5-minute ephemeral cache of the reusable prompt prefix.
    * ``"long"`` — 1-hour cache where the backend supports an extended TTL,
      otherwise equivalent to ``"short"``.

    The intent is the standard agentic-loop pattern: mark the stable prefix
    (system prompt / tool definitions) and the last message so each turn
    reuses the previous turn's prefix instead of re-billing the whole
    context. Agentic loops resend the full context every turn, so a cached
    prefix is a large input-cost lever.

    This is a documented *convention*, not an abstract-method contract: the
    :class:`Provider` ABC declares no ``__init__``, so each concrete provider
    accepts ``cache`` in its own constructor and applies it when building the
    request. Providers whose backend has no cache concept ignore it. A
    provider that ignores ``cache`` remains correct (it simply pays full
    price), exactly like the cooperative ``cancel_event`` convention below.
    """

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: ThinkingLevel | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Response:
        """Send messages, get a response.

        Args:
            cancel_event: Optional :class:`threading.Event`. When set, the
                provider should make a best-effort attempt to abort its
                in-flight HTTP request and raise. Subclasses that ignore
                the parameter remain correct (cooperative).
        """

    def _supports_cancel_event(self, method_name: str) -> bool:
        """Return ``True`` iff *method_name* on ``self`` accepts ``cancel_event``.

        Used by the default :meth:`stream` / :meth:`async_complete` /
        :meth:`async_stream` impls to decide whether to forward the
        ``cancel_event`` kwarg. Subclasses written before the cancel-event
        parameter existed keep working unchanged: we just drop the kwarg
        when introspection says it isn't supported.
        """
        import inspect

        method = getattr(self, method_name, None)
        if method is None:
            return False
        try:
            sig = inspect.signature(method)
        except (TypeError, ValueError):
            return False
        params = sig.parameters
        if "cancel_event" in params:
            return True
        # Older overrides may use **kwargs catch-all.
        for p in params.values():
            if p.kind is inspect.Parameter.VAR_KEYWORD:
                return True
        return False

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: ThinkingLevel | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[StreamEvent]:
        """Stream a response as incremental events.

        Default implementation wraps :meth:`complete` — subclasses should
        override for true token-by-token streaming.
        """
        kwargs: dict[str, Any] = {
            "tools": tools,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "thinking": thinking,
        }
        if self._supports_cancel_event("complete"):
            kwargs["cancel_event"] = cancel_event
        response = self.complete(messages, **kwargs)
        if response.content:
            yield StreamEvent(type="text_delta", content=response.content)
        for tc in response.tool_calls:
            yield StreamEvent(type="tool_call_start", tool_call=tc)
        yield StreamEvent(type="done", usage=response.usage)

    async def async_complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: ThinkingLevel | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Response:
        """Async version of :meth:`complete`.

        Default wraps the synchronous ``complete()`` via
        :meth:`asyncio.loop.run_in_executor`.  Subclasses with native
        async SDKs should override for true non-blocking I/O.
        """
        loop = asyncio.get_running_loop()
        forward_cancel = self._supports_cancel_event("complete")

        def _call() -> Response:
            kwargs: dict[str, Any] = {
                "tools": tools,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "thinking": thinking,
            }
            if forward_cancel:
                kwargs["cancel_event"] = cancel_event
            return self.complete(messages, **kwargs)

        return await loop.run_in_executor(None, _call)

    async def async_stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: ThinkingLevel | None = None,
        cancel_event: threading.Event | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Async version of :meth:`stream`.

        Default bridges the synchronous ``stream()`` iterator into an
        :class:`AsyncIterator` via a background thread and a queue.
        Subclasses with native async SDKs should override.
        """
        queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        forward_cancel = self._supports_cancel_event("stream")

        def _produce() -> None:
            try:
                kwargs: dict[str, Any] = {
                    "tools": tools,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "thinking": thinking,
                }
                if forward_cancel:
                    kwargs["cancel_event"] = cancel_event
                for event in self.stream(messages, **kwargs):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(None, _produce)

        while True:
            event = await queue.get()
            if event is None:
                break
            yield event

    @property
    @abstractmethod
    def context_window(self) -> int:
        """Maximum context window size in tokens."""

    @property
    @abstractmethod
    def supports_tool_use(self) -> bool:
        """Whether this provider supports function calling."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The model identifier."""
