"""Tests for chimera.bridge.jsonrpc — JSON-RPC 2.0 bridge protocol."""
from __future__ import annotations

import asyncio

import pytest

from chimera.bridge.jsonrpc import JsonRpcBridge, JsonRpcRequest, JsonRpcResponse, RPC_METHODS


class TestJsonRpcResponse:

    def test_jsonrpc_response_to_dict(self) -> None:
        """Response.to_dict() produces a valid JSON-RPC 2.0 envelope."""
        resp = JsonRpcResponse(result={"status": "ok"}, id=1)
        d = resp.to_dict()
        assert d["jsonrpc"] == "2.0"
        assert d["id"] == 1
        assert d["result"] == {"status": "ok"}
        assert "error" not in d

    def test_jsonrpc_response_error_to_dict(self) -> None:
        """Error responses include 'error' key instead of 'result'."""
        resp = JsonRpcResponse(
            error={"code": -32601, "message": "Method not found"},
            id=42,
        )
        d = resp.to_dict()
        assert d["jsonrpc"] == "2.0"
        assert d["id"] == 42
        assert d["error"]["code"] == -32601
        assert "result" not in d or d.get("result") is None


class TestJsonRpcBridge:

    @pytest.mark.asyncio
    async def test_handle_request_calls_handler(self) -> None:
        """handle_request dispatches to the registered handler and returns its result."""
        bridge = JsonRpcBridge()

        async def echo_handler(text: str = "") -> str:
            return f"echo: {text}"

        bridge.register("echo", echo_handler)

        req = JsonRpcRequest(method="echo", params={"text": "hello"}, id=1)
        resp = await bridge.handle_request(req)

        assert resp.result == "echo: hello"
        assert resp.error is None
        assert resp.id == 1

    @pytest.mark.asyncio
    async def test_handle_unknown_method_returns_error(self) -> None:
        """Calling an unregistered method returns a -32601 error."""
        bridge = JsonRpcBridge()

        req = JsonRpcRequest(method="nonexistent", params={}, id=2)
        resp = await bridge.handle_request(req)

        assert resp.error is not None
        assert resp.error["code"] == -32601
        assert "nonexistent" in resp.error["message"]
        assert resp.id == 2

    @pytest.mark.asyncio
    async def test_handle_request_exception_returns_error(self) -> None:
        """If a handler raises, the bridge returns a -32000 internal error."""
        bridge = JsonRpcBridge()

        async def boom() -> None:
            raise RuntimeError("kaboom")

        bridge.register("boom", boom)

        req = JsonRpcRequest(method="boom", params={}, id=3)
        resp = await bridge.handle_request(req)

        assert resp.error is not None
        assert resp.error["code"] == -32000
        assert "kaboom" in resp.error["message"]
        assert resp.id == 3

    @pytest.mark.asyncio
    async def test_register_and_handle(self) -> None:
        """Multiple methods can be registered and dispatched independently."""
        bridge = JsonRpcBridge()

        async def add(a: int = 0, b: int = 0) -> int:
            return a + b

        async def greet(name: str = "world") -> str:
            return f"hello {name}"

        bridge.register("add", add)
        bridge.register("greet", greet)

        resp1 = await bridge.handle_request(
            JsonRpcRequest(method="add", params={"a": 2, "b": 3}, id=10)
        )
        resp2 = await bridge.handle_request(
            JsonRpcRequest(method="greet", params={"name": "chimera"}, id=11)
        )

        assert resp1.result == 5
        assert resp2.result == "hello chimera"

    def test_rpc_methods_defined(self) -> None:
        """RPC_METHODS dict contains the expected standard method names."""
        expected = {
            "prompt", "abort", "steer", "follow_up",
            "get_status", "list_tools", "set_model", "set_thinking",
            "compact", "get_session", "fork", "export",
        }
        assert expected == set(RPC_METHODS.keys())

    @pytest.mark.asyncio
    async def test_serve_processes_requests(self) -> None:
        """serve() reads JSON lines from a reader and writes responses."""
        import json

        bridge = JsonRpcBridge()

        async def ping() -> str:
            return "pong"

        bridge.register("ping", ping)

        reader = asyncio.StreamReader()
        request_line = json.dumps({"jsonrpc": "2.0", "method": "ping", "params": {}, "id": 1}) + "\n"
        reader.feed_data(request_line.encode())
        reader.feed_eof()

        # Capture stdout
        import io
        import sys

        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured

        try:
            await bridge.serve(reader=reader)
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue().strip()
        resp = json.loads(output)
        assert resp["jsonrpc"] == "2.0"
        assert resp["result"] == "pong"
        assert resp["id"] == 1

    def test_stop(self) -> None:
        """stop() sets _running to False."""
        bridge = JsonRpcBridge()
        bridge._running = True
        bridge.stop()
        assert bridge._running is False
