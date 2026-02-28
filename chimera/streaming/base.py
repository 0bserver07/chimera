# chimera/streaming/base.py
"""Enhanced StreamHandler ABC for the streaming module."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.providers.base import StreamEvent

__all__ = ["StreamHandler"]


class StreamHandler(ABC):
    """Base class for handling streaming agent output.

    Subclasses must implement all ``on_*`` hooks.  The concrete
    :meth:`handle_event` method dispatches a :class:`StreamEvent` to
    the appropriate hook automatically.
    """

    @abstractmethod
    def on_text(self, text: str) -> None:
        """Called when a text content delta is received."""

    @abstractmethod
    def on_tool_start(self, tool_name: str, call_id: str) -> None:
        """Called when a tool call begins."""

    @abstractmethod
    def on_tool_end(self, call_id: str, output: str) -> None:
        """Called when a tool call completes with its output."""

    @abstractmethod
    def on_step_start(self, step: int) -> None:
        """Called at the beginning of a ReAct step."""

    @abstractmethod
    def on_step_end(self, step: int) -> None:
        """Called at the end of a ReAct step."""

    @abstractmethod
    def on_done(self) -> None:
        """Called when the entire streaming run is complete."""

    def handle_event(self, event: StreamEvent) -> None:
        """Dispatch a :class:`StreamEvent` to the appropriate handler method."""
        if event.type == "text_delta":
            self.on_text(event.content)
        elif event.type == "tool_call_start" and event.tool_call:
            self.on_tool_start(event.tool_call.name, event.tool_call.id)
        elif event.type == "done":
            self.on_done()
