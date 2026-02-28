# chimera/streaming/protocol.py
"""Protocol describing a provider that supports streaming."""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterator, Protocol

if TYPE_CHECKING:
    from chimera.providers.base import StreamEvent, ToolSchema
    from chimera.types import Message

__all__ = ["StreamingProvider"]


class StreamingProvider(Protocol):
    """Structural subtype for providers that expose a ``stream()`` method."""

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Iterator[StreamEvent]: ...
