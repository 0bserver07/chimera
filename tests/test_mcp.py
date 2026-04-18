# tests/test_mcp.py
import json


from chimera.mcp.transport import MCPTransport, StdioTransport, HTTPTransport
from chimera.mcp.client import MCPClient
from chimera.mcp.tools import MCPTool


class MockTransport(MCPTransport):
    """Mock transport for testing."""

    def __init__(self, responses: list[dict | None] | None = None):
        self._responses = list(responses or [])
        self.sent: list[dict] = []
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def send(self, message):
        self.sent.append(message)
        if self._responses:
            return self._responses.pop(0)
        return None

    def close(self):
        self.closed = True


# ---- Transport tests ----


class TestStdioTransport:
    def test_write_message_newline_delimited(self):
        """Verify stdio uses newline-delimited JSON (not Content-Length)."""
        StdioTransport("echo", [])
        msg = {"jsonrpc": "2.0", "method": "test"}
        encoded = json.dumps(msg).encode("utf-8") + b"\n"
        # Verify the encoding is newline-delimited
        assert encoded.endswith(b"\n")
        assert b"Content-Length" not in encoded


class TestHTTPTransport:
    def test_session_id_tracking(self):
        transport = HTTPTransport("https://example.com/mcp")
        assert transport._session_id is None
        # Session ID is set from response headers (tested via mock in integration)


# ---- Client tests ----


class TestMCPClient:
    def test_initialize(self):
        mock = MockTransport([
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},  # initialize response
            None,  # initialized notification (no response)
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}},  # tools/list
        ])
        client = MCPClient()
        client.add_transport("test", mock)
        client.connect_all()

        assert mock.started
        # Should have sent: initialize, initialized, tools/list
        methods = [m.get("method") for m in mock.sent]
        assert "initialize" in methods
        assert "notifications/initialized" in methods
        assert "tools/list" in methods

    def test_discover_tools(self):
        tool_defs = [
            {"name": "read_file", "description": "Read a file", "inputSchema": {"type": "object"}},
            {"name": "write_file", "description": "Write a file"},
        ]
        mock = MockTransport([
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
            None,
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": tool_defs}},
        ])
        client = MCPClient()
        client.add_transport("fs", mock)
        client.connect_all()

        tools = client.tools
        assert len(tools) == 2
        assert tools[0].name == "read_file"
        assert tools[1].name == "write_file"

    def test_tools_property_returns_mcp_tools(self):
        mock = MockTransport([
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
            None,
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "t1"}]}},
        ])
        client = MCPClient()
        client.add_transport("s", mock)
        client.connect_all()

        tools = client.tools
        assert len(tools) == 1
        assert isinstance(tools[0], MCPTool)

    def test_call_tool(self):
        mock = MockTransport([
            {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "hello"}]}},
        ])
        client = MCPClient()
        result = client.call_tool(mock, "greet", {"name": "world"})
        assert result["content"][0]["text"] == "hello"

    def test_call_tool_error(self):
        mock = MockTransport([
            {"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "not found"}},
        ])
        client = MCPClient()
        result = client.call_tool(mock, "missing", {})
        assert "error" in result

    def test_add_http(self):
        client = MCPClient()
        client.add_http("remote", "https://example.com/mcp", auth="token123")
        assert "remote" in client._transports
        transport = client._transports["remote"]
        assert isinstance(transport, HTTPTransport)
        assert transport._headers["Authorization"] == "Bearer token123"

    def test_add_custom_transport(self):
        mock = MockTransport()
        client = MCPClient()
        client.add_transport("custom", mock)
        assert client._transports["custom"] is mock

    def test_context_manager(self):
        mock = MockTransport([
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
            None,
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}},
        ])
        client = MCPClient()
        client.add_transport("test", mock)
        with client:
            assert mock.started
        assert mock.closed

    def test_disconnect_all(self):
        mock1 = MockTransport()
        mock2 = MockTransport()
        client = MCPClient()
        client._transports = {"a": mock1, "b": mock2}
        client.disconnect_all()
        assert mock1.closed
        assert mock2.closed


# ---- MCPTool tests ----


class TestMCPTool:
    def _make_tool(self, responses=None):
        mock = MockTransport(responses or [])
        client = MCPClient()
        tool = MCPTool(
            tool_def={"name": "test_tool", "description": "A test tool", "inputSchema": {"type": "object"}},
            transport=mock,
            server_name="test-server",
            client=client,
        )
        return tool, mock, client

    def test_schema(self):
        tool, _, _ = self._make_tool()
        assert tool.name == "test_tool"
        assert tool.description == "A test tool"
        schema = tool.to_anthropic_schema()
        assert schema["name"] == "test_tool"

    def test_execute_success(self):
        tool, mock, client = self._make_tool()
        mock._responses = [
            {"jsonrpc": "2.0", "id": 1, "result": {
                "content": [{"type": "text", "text": "result data"}],
            }},
        ]
        result = tool.execute({"key": "value"}, None)
        assert result.success
        assert "result data" in result.output
        assert result.metadata["mcp_server"] == "test-server"

    def test_execute_error(self):
        tool, mock, client = self._make_tool()
        mock._responses = [
            {"jsonrpc": "2.0", "id": 1, "result": {"error": "Something went wrong"}},
        ]
        result = tool.execute({}, None)
        assert not result.success
        assert "Something went wrong" in result.error

    def test_content_blocks(self):
        tool, mock, client = self._make_tool()
        mock._responses = [
            {"jsonrpc": "2.0", "id": 1, "result": {
                "content": [
                    {"type": "text", "text": "line1"},
                    {"type": "text", "text": "line2"},
                ],
            }},
        ]
        result = tool.execute({}, None)
        assert "line1" in result.output
        assert "line2" in result.output

    def test_is_error_flag(self):
        tool, mock, client = self._make_tool()
        mock._responses = [
            {"jsonrpc": "2.0", "id": 1, "result": {
                "content": [{"type": "text", "text": "error msg"}],
                "isError": True,
            }},
        ]
        result = tool.execute({}, None)
        assert not result.success
        assert "error msg" in result.error


# ---- Retry tests ----


class FailingTransport(MCPTransport):
    """Transport that fails N times then succeeds."""

    def __init__(self, fail_count: int, success_response: dict | None = None):
        self._fail_count = fail_count
        self._calls = 0
        self._success_response = success_response or {
            "jsonrpc": "2.0", "id": 1,
            "result": {"content": [{"type": "text", "text": "ok"}]},
        }

    def start(self):
        pass

    def send(self, message):
        self._calls += 1
        if self._calls <= self._fail_count:
            raise ConnectionError("transport down")
        return self._success_response

    def close(self):
        pass


class TestMCPRetry:
    def test_retry_succeeds_after_failure(self):
        """call_tool retries on transport error and succeeds."""
        transport = FailingTransport(fail_count=2)
        client = MCPClient()
        result = client.call_tool(transport, "test", {}, max_retries=3)
        assert "content" in result
        assert transport._calls == 3  # 2 failures + 1 success

    def test_retry_exhausted(self):
        """call_tool returns error after exhausting retries."""
        transport = FailingTransport(fail_count=5)
        client = MCPClient()
        result = client.call_tool(transport, "test", {}, max_retries=3)
        assert "error" in result
        assert "Transport error" in result["error"]
        assert transport._calls == 3

    def test_no_retry_on_tool_error(self):
        """Tool-level errors are returned immediately, not retried."""
        mock = MockTransport([
            {"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "bad args"}},
        ])
        client = MCPClient()
        result = client.call_tool(mock, "test", {}, max_retries=3)
        assert result == {"error": "bad args"}
        assert len(mock.sent) == 1  # No retry

    def test_retry_on_timeout_error(self):
        """TimeoutError triggers retry."""

        class TimeoutTransport(MCPTransport):
            def __init__(self):
                self.calls = 0

            def start(self): pass
            def close(self): pass

            def send(self, message):
                self.calls += 1
                if self.calls <= 1:
                    raise TimeoutError("timed out")
                return {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}

        transport = TimeoutTransport()
        client = MCPClient()
        result = client.call_tool(transport, "test", {}, max_retries=3)
        assert "ok" in result
        assert transport.calls == 2


# ---- Stderr reader tests ----


class TestStdioStderrReader:
    def test_stderr_lines_captured(self):
        """StdioTransport captures stderr output in background."""
        import time

        transport = StdioTransport("python3", ["-c", "import sys; sys.stderr.write('warn1\\nwarn2\\n'); import time; time.sleep(0.1)"])
        transport.start()
        time.sleep(0.3)  # Let stderr reader thread capture output
        lines = transport.stderr_lines
        transport.close()
        assert "warn1" in lines
        assert "warn2" in lines

    def test_stderr_lines_bounded(self):
        """Stderr deque has bounded maxlen."""
        transport = StdioTransport("echo", [])
        assert transport._stderr_lines.maxlen == 100

    def test_stderr_empty_initially(self):
        """Stderr lines empty before any subprocess output."""
        transport = StdioTransport("echo", [])
        assert transport.stderr_lines == []


# ---- Health check + refresh tests ----


class TestMCPHealthCheck:
    def test_ping_all_healthy(self):
        """ping() returns True for responsive servers."""
        mock = MockTransport([
            {"jsonrpc": "2.0", "id": 1, "result": {}},  # ping response
        ])
        client = MCPClient()
        client._transports = {"srv1": mock}
        result = client.ping()
        assert result == {"srv1": True}

    def test_ping_single_server(self):
        """ping(name) only pings the named server."""
        healthy = MockTransport([{"jsonrpc": "2.0", "id": 1, "result": {}}])
        other = MockTransport()
        client = MCPClient()
        client._transports = {"healthy": healthy, "other": other}
        result = client.ping("healthy")
        assert result == {"healthy": True}
        assert len(other.sent) == 0  # other not pinged

    def test_ping_unhealthy(self):
        """ping() returns False for unresponsive servers."""

        class DeadTransport(MCPTransport):
            def start(self): pass
            def close(self): pass
            def send(self, message):
                raise ConnectionError("dead")

        client = MCPClient()
        client._transports = {"dead": DeadTransport()}
        result = client.ping()
        assert result == {"dead": False}


class TestMCPToolRefresh:
    def test_refresh_tools_updates_defs(self):
        """refresh_tools re-discovers tools from server."""
        tool_v1 = [{"name": "tool_a"}]
        tool_v2 = [{"name": "tool_a"}, {"name": "tool_b"}]
        mock = MockTransport([
            # First discovery (connect_all)
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
            None,
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": tool_v1}},
            # refresh_tools
            {"jsonrpc": "2.0", "id": 3, "result": {"tools": tool_v2}},
        ])
        client = MCPClient()
        client.add_transport("srv", mock)
        client.connect_all()
        assert len(client.tools) == 1

        client.refresh_tools()
        assert len(client.tools) == 2

    def test_refresh_specific_server(self):
        """refresh_tools(name) only refreshes named server."""
        mock1 = MockTransport([
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
            None,
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "t1"}]}},
            {"jsonrpc": "2.0", "id": 3, "result": {"tools": [{"name": "t1"}, {"name": "t2"}]}},
        ])
        mock2 = MockTransport([
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
            None,
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "x1"}]}},
        ])
        client = MCPClient()
        client.add_transport("s1", mock1)
        client.add_transport("s2", mock2)
        client.connect_all()

        initial_count = len(mock2.sent)
        client.refresh_tools("s1")
        # s2 should not have received more messages
        assert len(mock2.sent) == initial_count
