"""MCP client -- manages server connections and discovers tools/resources."""
from __future__ import annotations

import time
from typing import Any

from chimera.mcp.transport import MCPTransport, StdioTransport, HTTPTransport


class MCPClient:
    """Manages connections to one or more MCP servers.

    Discovers tools and resources from connected servers, wrapping
    them as Chimera BaseTool instances.

    Example:
        ```python
        client = MCPClient()
        client.add_stdio("fs", "npx", ["-y", "@modelcontextprotocol/server-filesystem"])
        client.connect_all()
        agent = Agent(tools=DEFAULT_TOOLS + client.tools)
        ```
    """

    def __init__(self) -> None:
        self._transports: dict[str, MCPTransport] = {}
        self._tool_defs: dict[str, list[dict[str, Any]]] = {}
        self._request_id = 0

    def add_stdio(self, name: str, command: str, args: list[str] | None = None,
                  env: dict[str, str] | None = None) -> None:
        """Register a stdio MCP server.

        Args:
            name: Unique server name.
            command: Command to start the server.
            args: Command arguments.
            env: Environment variables for the subprocess.
        """
        self._transports[name] = StdioTransport(command, args, env)

    def add_http(self, name: str, url: str, auth: str | None = None) -> None:
        """Register an HTTP MCP server.

        Args:
            name: Unique server name.
            url: MCP endpoint URL.
            auth: Bearer token for authentication.
        """
        headers = {}
        if auth:
            headers["Authorization"] = f"Bearer {auth}"
        self._transports[name] = HTTPTransport(url, headers)

    def add_transport(self, name: str, transport: MCPTransport) -> None:
        """Register a custom transport.

        Args:
            name: Unique server name.
            transport: Transport instance.
        """
        self._transports[name] = transport

    def connect_all(self) -> None:
        """Start all transports and discover tools."""
        for name, transport in self._transports.items():
            transport.start()
            self._initialize(name, transport)
            self._discover_tools(name, transport)

    def disconnect_all(self) -> None:
        """Close all transport connections."""
        for transport in self._transports.values():
            transport.close()

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _initialize(self, name: str, transport: MCPTransport) -> None:
        """Send initialize request to an MCP server."""
        transport.send({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "chimera", "version": "0.1.0"},
            },
        })
        # Send initialized notification
        transport.send({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })

    def _discover_tools(self, name: str, transport: MCPTransport) -> None:
        """Discover tools from an MCP server."""
        response = transport.send({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
        })
        if response and "result" in response:
            self._tool_defs[name] = response["result"].get("tools", [])

    @property
    def tools(self) -> list:
        """All discovered tools as BaseTool instances."""
        from chimera.mcp.tools import MCPTool
        result = []
        for name, defs in self._tool_defs.items():
            transport = self._transports[name]
            for tool_def in defs:
                result.append(MCPTool(
                    tool_def=tool_def,
                    transport=transport,
                    server_name=name,
                    client=self,
                ))
        return result

    def call_tool(
        self,
        transport: MCPTransport,
        tool_name: str,
        arguments: dict[str, Any],
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Call a tool on an MCP server with retry on transport errors.

        Retries up to *max_retries* times with exponential backoff (1s, 2s, 4s)
        on transport-level errors (``ConnectionError``, ``TimeoutError``,
        ``OSError``).  Tool-level errors (returned in the JSON-RPC response)
        are **not** retried.

        Args:
            transport: Transport to the server.
            tool_name: Name of the tool to call.
            arguments: Tool arguments.
            max_retries: Maximum retry attempts (default 3).

        Returns:
            Tool result dict.
        """
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                response = transport.send({
                    "jsonrpc": "2.0",
                    "id": self._next_id(),
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                })
                if response and "result" in response:
                    return response["result"]
                if response and "error" in response:
                    # Tool-level error — don't retry
                    return {"error": response["error"].get("message", "Unknown error")}
                return {"error": "No response from MCP server"}
            except (ConnectionError, TimeoutError, OSError) as exc:
                last_exc = exc
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # 1s, 2s, 4s
        return {"error": f"Transport error after {max_retries} attempts: {last_exc}"}

    def ping(self, name: str | None = None) -> dict[str, bool]:
        """Check whether MCP servers are responsive.

        Sends a ``ping`` JSON-RPC request to each server (or a specific one)
        and returns a mapping of server name to reachability.

        Args:
            name: If given, only ping this server. Otherwise ping all.

        Returns:
            ``{server_name: True/False}`` for each server tested.
        """
        targets = (
            {name: self._transports[name]}
            if name and name in self._transports
            else dict(self._transports)
        )
        results: dict[str, bool] = {}
        for srv_name, transport in targets.items():
            try:
                transport.send({
                    "jsonrpc": "2.0",
                    "id": self._next_id(),
                    "method": "ping",
                })
                results[srv_name] = True
            except Exception:
                results[srv_name] = False
        return results

    def refresh_tools(self, name: str | None = None) -> None:
        """Re-discover tools from one or all connected servers.

        Useful after a server restarts or updates its tool list.

        Args:
            name: If given, refresh only this server. Otherwise refresh all.
        """
        targets = (
            {name: self._transports[name]}
            if name and name in self._transports
            else dict(self._transports)
        )
        for srv_name, transport in targets.items():
            self._discover_tools(srv_name, transport)

    def __enter__(self) -> MCPClient:
        self.connect_all()
        return self

    def __exit__(self, *args: object) -> None:
        self.disconnect_all()
