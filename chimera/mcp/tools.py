"""MCP tool wrappers -- wraps MCP tools as Chimera BaseTool instances."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from chimera.core.tool import BaseTool
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.env.base import Environment
    from chimera.mcp.client import MCPClient
    from chimera.mcp.transport import MCPTransport

_ACTIVE_CLIENTS: list = []  # Hold references to prevent GC


class MCPTool(BaseTool):
    """Wraps an MCP tool definition as a Chimera BaseTool.

    Created automatically by MCPClient.tools -- not typically
    instantiated directly.
    """

    def __init__(
        self,
        tool_def: dict[str, Any],
        transport: MCPTransport,
        server_name: str,
        client: MCPClient,
    ) -> None:
        self.name = tool_def.get("name", "unknown")
        self.description = tool_def.get("description", "")
        self.parameters = tool_def.get("inputSchema", {
            "type": "object", "properties": {},
        })
        self._transport = transport
        self._server_name = server_name
        self._client = client

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        """Execute the MCP tool by calling the remote server.

        Args:
            args: Tool arguments.
            env: Execution environment (unused -- MCP tools manage their own state).

        Returns:
            ToolResult with the server's response.
        """
        try:
            result = self._client.call_tool(self._transport, self.name, args)
        except Exception as e:
            return ToolResult(output="", error=f"MCP tool error: {e}")

        if "error" in result:
            return ToolResult(output="", error=result["error"])

        # MCP returns content as list of content blocks
        content = result.get("content", [])
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    text_parts.append(block)
            output = "\n".join(text_parts)
        else:
            output = str(content)

        is_error = result.get("isError", False)
        return ToolResult(
            output=output,
            error=output if is_error else None,
            metadata={"mcp_server": self._server_name},
        )


class MCPToolSource:
    """Convenience wrapper for quickly connecting to an MCP server.

    Example:
        ```python
        tools = MCPToolSource.from_stdio("npx", ["-y", "@mcp/server-fs"])
        agent = Agent(tools=DEFAULT_TOOLS + tools)
        ```
    """

    @staticmethod
    def from_stdio(command: str, args: list[str] | None = None,
                   env: dict[str, str] | None = None) -> list[BaseTool]:
        """Connect to a stdio MCP server and return its tools.

        Args:
            command: Command to start the server.
            args: Command arguments.
            env: Environment variables.

        Returns:
            List of BaseTool instances wrapping the server's tools.
        """
        from chimera.mcp.client import MCPClient
        client = MCPClient()
        client.add_stdio("default", command, args, env)
        client.connect_all()
        _ACTIVE_CLIENTS.append(client)  # prevent GC
        return client.tools

    @staticmethod
    def from_config(config: dict) -> tuple[MCPClient, list[BaseTool]]:
        """Load MCP servers from a config dict.

        Config format::

            {"servers": {"name": {"command": "...", "args": [...]}}}

        or::

            {"servers": {"name": {"url": "https://..."}}}

        Args:
            config: Dictionary with a ``servers`` key mapping server names
                to their connection parameters.

        Returns:
            ``(client, tools)`` tuple. Caller owns the client lifecycle.
        """
        from chimera.mcp.client import MCPClient
        client = MCPClient()
        servers = config.get("servers", {})
        for name, server_config in servers.items():
            if "command" in server_config:
                client.add_stdio(
                    name,
                    server_config["command"],
                    server_config.get("args"),
                    server_config.get("env"),
                )
            elif "url" in server_config:
                client.add_http(name, server_config["url"], server_config.get("auth"))
        client.connect_all()
        _ACTIVE_CLIENTS.append(client)
        return client, client.tools
