"""JSON-RPC 2.0 bridge over stdin/stdout for IDE integration.

Provides a lightweight JSON-RPC server that reads newline-delimited JSON
requests from stdin and writes responses to stdout.  Designed for use by
IDE plugins (Codex, Pi-mono, etc.) that need a headless control channel
to a running Chimera agent.
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


@dataclass
class JsonRpcRequest:
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: int | str | None = None


@dataclass
class JsonRpcResponse:
    result: Any = None
    error: dict[str, Any] | None = None
    id: int | str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"jsonrpc": "2.0", "id": self.id}
        if self.error:
            d["error"] = self.error
        else:
            d["result"] = self.result
        return d


class JsonRpcBridge:
    """JSON-RPC 2.0 bridge over stdin/stdout for IDE integration.

    Methods the bridge supports:
    - prompt: Send a user message to the agent
    - abort: Cancel the current run
    - steer: Inject a steering message mid-run
    - get_status: Get agent status
    - list_tools: List available tools
    - set_model: Change the model
    - compact: Trigger context compaction
    - get_session: Get session info

    Usage::

        bridge = JsonRpcBridge()
        bridge.register("prompt", handle_prompt)
        bridge.register("abort", handle_abort)
        await bridge.serve()
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[..., Awaitable[Any]]] = {}
        self._running = False

    def register(self, method: str, handler: Callable[..., Awaitable[Any]]) -> None:
        """Register a handler for a JSON-RPC method."""
        self._handlers[method] = handler

    async def handle_request(self, request: JsonRpcRequest) -> JsonRpcResponse:
        """Handle a single JSON-RPC request."""
        handler = self._handlers.get(request.method)
        if handler is None:
            return JsonRpcResponse(
                error={"code": -32601, "message": f"Method not found: {request.method}"},
                id=request.id,
            )
        try:
            result = await handler(**request.params)
            return JsonRpcResponse(result=result, id=request.id)
        except Exception as e:
            return JsonRpcResponse(
                error={"code": -32000, "message": str(e)},
                id=request.id,
            )

    async def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        line = json.dumps(msg) + "\n"
        sys.stdout.write(line)
        sys.stdout.flush()

    async def send_response(self, response: JsonRpcResponse) -> None:
        """Send a JSON-RPC response."""
        line = json.dumps(response.to_dict()) + "\n"
        sys.stdout.write(line)
        sys.stdout.flush()

    async def serve(self, reader: asyncio.StreamReader | None = None) -> None:
        """Serve JSON-RPC requests from stdin (or provided reader)."""
        self._running = True

        if reader is None:
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)
            await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

        while self._running:
            try:
                line = await reader.readline()
                if not line:
                    break

                data = json.loads(line.decode().strip())
                request = JsonRpcRequest(
                    method=data.get("method", ""),
                    params=data.get("params", {}),
                    id=data.get("id"),
                )

                response = await self.handle_request(request)
                if request.id is not None:  # Only send response for requests (not notifications)
                    await self.send_response(response)

            except json.JSONDecodeError:
                continue
            except Exception:
                break

        self._running = False

    def stop(self) -> None:
        """Signal the serve loop to stop."""
        self._running = False


# Standard method names (matching Codex/Pi conventions)
RPC_METHODS = {
    "prompt": "Send a user message",
    "abort": "Cancel current run",
    "steer": "Inject steering message",
    "follow_up": "Add follow-up message",
    "get_status": "Get agent status",
    "list_tools": "List available tools",
    "set_model": "Change model",
    "set_thinking": "Set thinking level",
    "compact": "Trigger compaction",
    "get_session": "Get session info",
    "fork": "Fork session",
    "export": "Export session",
}
