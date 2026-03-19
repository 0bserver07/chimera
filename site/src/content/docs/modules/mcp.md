---
title: "MCP (Model Context Protocol)"
description: "MCP (Model Context Protocol)"
---

Chimera's MCP module lets agents connect to external MCP servers and use their tools as native Chimera `BaseTool` instances. This enables agents to interact with databases, filesystems, APIs, and any other service that exposes an MCP interface -- without writing custom tool code.

## Quick Start

```python
from chimera.mcp import MCPClient
from chimera.core.agent import Agent
from chimera.core.tool_group import DEFAULT_TOOLS

client = MCPClient()
client.add_stdio("fs", "npx", ["-y", "@modelcontextprotocol/server-filesystem"])
client.connect_all()

agent = Agent(tools=list(DEFAULT_TOOLS) + client.tools)
result = agent.run("List files in the current directory")

client.disconnect_all()
```

## Key Classes

| Class | Description |
|-------|-------------|
| `MCPClient` | Manages connections to one or more MCP servers. Discovers tools and exposes them as `BaseTool` instances. |
| `MCPTool` | Wraps an individual MCP tool definition as a Chimera `BaseTool`. Created automatically by `MCPClient.tools`. |
| `MCPToolSource` | Convenience class with static methods for quickly connecting to MCP servers and getting tools. |
| `MCPTransport` | Abstract base class for MCP transport implementations (JSON-RPC 2.0). |
| `StdioTransport` | Transport that communicates via stdin/stdout of a subprocess using newline-delimited JSON. |
| `HTTPTransport` | Transport that communicates via HTTP POST requests to an MCP endpoint. |

## Usage

### Managing multiple servers with MCPClient

`MCPClient` is the primary interface. Register servers, connect, and retrieve tools:

```python
from chimera.mcp import MCPClient

client = MCPClient()

# Add a stdio-based server
client.add_stdio("filesystem", "npx", ["-y", "@mcp/server-fs"])

# Add an HTTP-based server
client.add_http("api", "https://mcp.example.com/v1", auth="sk-my-token")

# Connect to all servers and discover tools
client.connect_all()

# Access all discovered tools as BaseTool instances
tools = client.tools

# Ping servers to check health
status = client.ping()           # {"filesystem": True, "api": True}
status = client.ping("api")      # {"api": True}

# Re-discover tools after a server update
client.refresh_tools()
client.refresh_tools("filesystem")  # Refresh a specific server

# Disconnect when done
client.disconnect_all()
```

`MCPClient` also supports context manager usage:

```python
with MCPClient() as client:
    client.add_stdio("fs", "npx", ["-y", "@mcp/server-fs"])
    # connect_all() is called automatically on __enter__
    agent = Agent(tools=list(DEFAULT_TOOLS) + client.tools)
    result = agent.run("Read the README.md file")
# disconnect_all() is called automatically on __exit__
```

### Quick one-liner with MCPToolSource

For simple cases where you just need tools from a single server:

```python
from chimera.mcp import MCPToolSource
from chimera.core.agent import Agent
from chimera.core.tool_group import DEFAULT_TOOLS

# One-liner: connect to a stdio server and get tools
tools = MCPToolSource.from_stdio("npx", ["-y", "@mcp/server-fs"])
agent = Agent(tools=list(DEFAULT_TOOLS) + tools)
```

### Loading from configuration

Load MCP servers from a config dict (matches `.mcp.json` format):

```python
from chimera.mcp import MCPToolSource

config = {
    "servers": {
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@mcp/server-fs"],
        },
        "api": {
            "url": "https://mcp.example.com/v1",
            "auth": "sk-my-token",
        },
    }
}

client, tools = MCPToolSource.from_config(config)
```

### Custom transports

Implement `MCPTransport` to create a custom transport:

```python
from chimera.mcp import MCPTransport, MCPClient
from typing import Any

class WebSocketTransport(MCPTransport):
    def __init__(self, url: str) -> None:
        self._url = url

    def start(self) -> None:
        # Open WebSocket connection
        ...

    def send(self, message: dict[str, Any]) -> dict[str, Any] | None:
        # Send JSON-RPC message and return response
        ...

    def close(self) -> None:
        # Close connection
        ...

client = MCPClient()
client.add_transport("custom", WebSocketTransport("ws://localhost:8080"))
client.connect_all()
```

### Calling tools with retry

`MCPClient.call_tool()` includes built-in retry logic with exponential backoff (1s, 2s, 4s) for transport-level errors (`ConnectionError`, `TimeoutError`, `OSError`). Tool-level errors returned in the JSON-RPC response are not retried.

```python
result = client.call_tool(
    transport=transport,
    tool_name="read_file",
    arguments={"path": "/tmp/data.txt"},
    max_retries=3,
)
```

## Integration

- **Agent tools**: `MCPClient.tools` returns `MCPTool` instances that extend `BaseTool`. Add them to any agent's tool list alongside `DEFAULT_TOOLS`.
- **REPL**: The `chimera code` REPL automatically loads MCP servers from `~/.chimera/mcp.json` on startup.
- **Plugin system**: MCP servers can be configured via `.mcp.json` in the project root or through the plugin directory loader (`chimera.plugins.dir_loader`).
- **Config format**: The `.mcp.json` file uses the `{"servers": {"name": {"command": "...", "args": [...]}}}` format, supporting both stdio and HTTP servers.

## Import Reference

```python
from chimera.mcp import MCPClient, MCPTool, MCPToolSource
from chimera.mcp import MCPTransport, StdioTransport, HTTPTransport
from chimera.mcp.tools import MCPToolSource  # from_stdio(), from_config()
```
