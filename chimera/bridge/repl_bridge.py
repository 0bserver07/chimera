"""REPL bridge for connecting a REPL-style interface to an agent.

Translates :class:`LoopEvent` instances into bridge messages and routes
user input back to the agent via an :class:`asyncio.Queue`.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator

from chimera.bridge.protocol import BridgeProtocol
from chimera.core.loop_events import LoopEvent, LoopEventType

__all__ = ["REPLBridge", "BRIDGE_MESSAGES"]

# Standard bridge message types
BRIDGE_MESSAGES = {
    "session_start": "New session started",
    "session_end": "Session ended",
    "user_message": "User sent a message",
    "assistant_message": "Assistant response chunk",
    "assistant_complete": "Full assistant response",
    "tool_use": "Tool being executed",
    "tool_result": "Tool execution result",
    "permission_request": "Permission needed",
    "permission_response": "User responded to permission",
    "file_changed": "File was modified",
    "status_update": "Status change (model, cost, etc.)",
    "error": "Error occurred",
}


class REPLBridge:
    """Bridge between a REPL interface and an agent running via AgentLoop.

    Translates LoopEvents into bridge messages and routes user input
    back to the agent. This is chimera's equivalent of Claude Code's
    replBridge.ts (though much simpler).
    """

    def __init__(self, protocol: BridgeProtocol) -> None:
        self._protocol = protocol
        self._user_input_queue: asyncio.Queue[str] = asyncio.Queue()
        self._running = False
        self._last_permission_response: str = "deny_once"

        # Register handlers for incoming messages
        protocol.on_message("user_message", self._handle_user_message)
        protocol.on_message("permission_response", self._handle_permission_response)

    async def _handle_user_message(self, data: dict[str, Any]) -> None:
        """Route user input from bridge to the agent."""
        text = data.get("text", "")
        await self._user_input_queue.put(text)

    async def _handle_permission_response(self, data: dict[str, Any]) -> None:
        """Handle permission decision from the bridge UI."""
        self._last_permission_response = data.get("decision", "deny_once")

    async def forward_events(self, events: AsyncGenerator[LoopEvent, None]) -> None:
        """Forward LoopEvents from AgentLoop to the bridge protocol."""
        self._running = True
        await self._protocol.send("session_start", {"timestamp": asyncio.get_event_loop().time()})

        try:
            async for event in events:
                msg = self._event_to_message(event)
                if msg:
                    await self._protocol.send(msg["type"], msg["data"])
        finally:
            self._running = False
            await self._protocol.send("session_end", {"timestamp": asyncio.get_event_loop().time()})

    def _event_to_message(self, event: LoopEvent) -> dict[str, Any] | None:
        """Convert a LoopEvent to a bridge message."""
        if event.type == LoopEventType.assistant:
            return {
                "type": "assistant_complete",
                "data": {"content": getattr(event.data, "content", str(event.data)), "turn": event.turn},
            }
        elif event.type == LoopEventType.assistant_chunk:
            return {
                "type": "assistant_message",
                "data": {"chunk": str(event.data), "turn": event.turn},
            }
        elif event.type == LoopEventType.tool_use:
            return {
                "type": "tool_use",
                "data": {"tool": str(event.data), "turn": event.turn},
            }
        elif event.type == LoopEventType.tool_result:
            tc, result = event.data if isinstance(event.data, tuple) else (None, event.data)
            return {
                "type": "tool_result",
                "data": {
                    "tool": getattr(tc, "name", "unknown") if tc else "unknown",
                    "output": getattr(result, "output", str(result))[:1000],
                    "success": getattr(result, "success", True),
                    "turn": event.turn,
                },
            }
        elif event.type == LoopEventType.error:
            return {
                "type": "error",
                "data": {"message": str(event.data), "turn": event.turn},
            }
        elif event.type == LoopEventType.result:
            return {
                "type": "status_update",
                "data": {
                    "status": "completed",
                    "reason": getattr(event.data, "reason", "unknown"),
                    "turn_count": getattr(event.data, "turn_count", 0),
                    "cost_usd": getattr(event.data, "cost_usd", 0.0),
                },
            }
        return None

    async def get_user_input(self, timeout: float | None = None) -> str | None:
        """Wait for user input from the bridge."""
        try:
            if timeout:
                return await asyncio.wait_for(self._user_input_queue.get(), timeout)
            return await self._user_input_queue.get()
        except asyncio.TimeoutError:
            return None

    @property
    def is_running(self) -> bool:
        """Whether the bridge is currently forwarding events."""
        return self._running
