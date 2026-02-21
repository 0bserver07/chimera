# chimera/core/streaming.py
from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from chimera.providers.base import StreamEvent


class StreamHandler(ABC):
    """Base class for handling streaming agent output."""

    @abstractmethod
    def on_text(self, text: str) -> None:
        """Called when text content is streamed."""

    @abstractmethod
    def on_tool_start(self, tool_name: str, call_id: str) -> None:
        """Called when a tool call begins."""

    @abstractmethod
    def on_tool_end(self, call_id: str, output: str) -> None:
        """Called when a tool call completes."""

    @abstractmethod
    def on_done(self) -> None:
        """Called when streaming is complete."""

    def handle_event(self, event: StreamEvent) -> None:
        """Dispatch a StreamEvent to the appropriate handler method."""
        if event.type == "text_delta":
            self.on_text(event.content)
        elif event.type == "tool_call_start" and event.tool_call:
            self.on_tool_start(event.tool_call.name, event.tool_call.id)
        elif event.type == "done":
            self.on_done()


class PrintStreamHandler(StreamHandler):
    """Prints streaming output to stdout (Claude Code-like experience)."""

    def on_text(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def on_tool_start(self, tool_name: str, call_id: str) -> None:
        print(f"\n> Running {tool_name}...", flush=True)

    def on_tool_end(self, call_id: str, output: str) -> None:
        if output.strip():
            # Show truncated output
            lines = output.strip().splitlines()
            if len(lines) > 10:
                shown = "\n".join(lines[:5] + ["...", f"({len(lines)} lines total)"] + lines[-3:])
            else:
                shown = output.strip()
            print(shown, flush=True)

    def on_done(self) -> None:
        print(flush=True)


class CollectStreamHandler(StreamHandler):
    """Collects all events for inspection/testing."""

    def __init__(self) -> None:
        self.text = ""
        self.events: list[dict] = []

    def on_text(self, text: str) -> None:
        self.text += text
        self.events.append({"type": "text", "content": text})

    def on_tool_start(self, tool_name: str, call_id: str) -> None:
        self.events.append({"type": "tool_start", "name": tool_name, "call_id": call_id})

    def on_tool_end(self, call_id: str, output: str) -> None:
        self.events.append({"type": "tool_end", "call_id": call_id, "output": output})

    def on_done(self) -> None:
        self.events.append({"type": "done"})
