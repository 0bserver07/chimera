---
title: "chimera.mcp"
description: "Reference for chimera.mcp — Model Context Protocol client (stdio/HTTP), tool source, transport."
---

`chimera.mcp` is the Model Context Protocol client. It launches MCP
servers (over stdio or HTTP) and exposes their tools to a Chimera agent.

## Top-level exports

```python
from chimera.mcp import (
    MCPClient,
    MCPToolSource,
    from_config,
)
```

| Symbol | Module | Purpose |
|---|---|---|
| `MCPClient` | `chimera.mcp.client` | JSON-RPC 2.0 client. Lifecycle: `initialize` → `list_tools` → `call_tool` → `shutdown`. |
| `MCPToolSource` | `chimera.mcp.tools` | Wraps an `MCPClient` so its tools appear as Chimera `BaseTool`s. |
| `from_config(config)` | `chimera.mcp` | Build an `MCPClient` from a `.mcp.json` server entry. |

Transport implementations live in `chimera.mcp.transport` (stdio
subprocess + HTTP+SSE).

## Bundled MCP servers

Six MCP servers ship under `chimera/mcp_servers/`:

| Server | Module |
|---|---|
| `chimera-search` | `chimera.mcp_servers.search_server` |
| `chimera-review` | `chimera.mcp_servers.review_server` |
| `chimera-testgen` | `chimera.mcp_servers.testgen_server` |
| `chimera-migration` | `chimera.mcp_servers.migration_server` |
| `chimera-rag` | `chimera.mcp_servers.rag_server` |
| `chimera-benchmark` | `chimera.mcp_servers.benchmark_server` |

All speak JSON-RPC 2.0 over stdin/stdout and follow the MCP spec.

## See also

- [Build a Plugin](/build-a-plugin/) for declaring MCP servers in
  `plugin.json` / `.mcp.json`.
