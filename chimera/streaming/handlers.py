# chimera/streaming/handlers.py
"""Concrete StreamHandler implementations."""
from __future__ import annotations

from typing import Any

from chimera.streaming.base import StreamHandler

__all__ = [
    "ConsoleStreamHandler",
    "CollectStreamHandler",
    "NullStreamHandler",
]


class ConsoleStreamHandler(StreamHandler):
    """Prints streaming output to stdout in a human-readable format."""

    def on_text(self, text: str) -> None:
        print(text, end="", flush=True)

    def on_tool_start(self, tool_name: str, call_id: str) -> None:
        print(f"\n[Tool: {tool_name}]", flush=True)

    def on_tool_end(self, call_id: str, output: str) -> None:
        print(f"[Result: {output[:200]}]", flush=True)

    def on_step_start(self, step: int) -> None:
        print(f"\n--- Step {step} ---", flush=True)

    def on_step_end(self, step: int) -> None:
        pass

    def on_done(self) -> None:
        print("\n", flush=True)


class CollectStreamHandler(StreamHandler):
    """Collects all events into a list for testing and inspection."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def on_text(self, text: str) -> None:
        self.events.append({"type": "text", "content": text})

    def on_tool_start(self, tool_name: str, call_id: str) -> None:
        self.events.append({
            "type": "tool_start",
            "tool_name": tool_name,
            "call_id": call_id,
        })

    def on_tool_end(self, call_id: str, output: str) -> None:
        self.events.append({
            "type": "tool_end",
            "call_id": call_id,
            "output": output,
        })

    def on_step_start(self, step: int) -> None:
        self.events.append({"type": "step_start", "step": step})

    def on_step_end(self, step: int) -> None:
        self.events.append({"type": "step_end", "step": step})

    def on_done(self) -> None:
        self.events.append({"type": "done"})


class NullStreamHandler(StreamHandler):
    """No-op handler -- silently discards every event."""

    def on_text(self, text: str) -> None:
        pass

    def on_tool_start(self, tool_name: str, call_id: str) -> None:
        pass

    def on_tool_end(self, call_id: str, output: str) -> None:
        pass

    def on_step_start(self, step: int) -> None:
        pass

    def on_step_end(self, step: int) -> None:
        pass

    def on_done(self) -> None:
        pass
