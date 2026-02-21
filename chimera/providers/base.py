from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from chimera.types import Message, ToolCall


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
    type: str  # "text_delta", "tool_call_start", "tool_call_delta", "done"
    content: str = ""
    tool_call: ToolCall | None = None


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
    ) -> Response:
        """Send messages, get a response."""

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
