from __future__ import annotations

import asyncio
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


class Provider(ABC):
    """LLM backend. Any class implementing complete() works."""

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: ThinkingLevel | None = None,
    ) -> Response:
        """Send messages, get a response."""

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: ThinkingLevel | None = None,
    ) -> Iterator[StreamEvent]:
        """Stream a response as incremental events.

        Default implementation wraps :meth:`complete` — subclasses should
        override for true token-by-token streaming.
        """
        response = self.complete(
            messages, tools=tools, temperature=temperature, max_tokens=max_tokens,
            thinking=thinking,
        )
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
    ) -> Response:
        """Async version of :meth:`complete`.

        Default wraps the synchronous ``complete()`` via
        :meth:`asyncio.loop.run_in_executor`.  Subclasses with native
        async SDKs should override for true non-blocking I/O.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.complete(
                messages, tools=tools, temperature=temperature, max_tokens=max_tokens,
                thinking=thinking,
            ),
        )

    async def async_stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: ThinkingLevel | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Async version of :meth:`stream`.

        Default bridges the synchronous ``stream()`` iterator into an
        :class:`AsyncIterator` via a background thread and a queue.
        Subclasses with native async SDKs should override.
        """
        queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _produce() -> None:
            try:
                for event in self.stream(
                    messages, tools=tools, temperature=temperature, max_tokens=max_tokens,
                    thinking=thinking,
                ):
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
