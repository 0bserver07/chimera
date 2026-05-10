---
title: "chimera.acp"
description: "Reference for chimera.acp — Agent Client Protocol (JSON-RPC 2.0 over stdio)."
---

`chimera.acp` implements the Agent Client Protocol so external agents
can be wrapped as Chimera tools.

## Top-level exports

```python
from chimera.acp import ACPClient, ACPSessionConfig, ExternalAgentTool
from chimera.acp.types import ACPToolCall, ACPResponse
```

| Symbol | Module | Purpose |
|---|---|---|
| `ACPSessionConfig` | `chimera.acp.types` | Dataclass: `command` (subprocess argv), `cwd`, `env`. |
| `ACPToolCall` / `ACPResponse` | `chimera.acp.types` | JSON-RPC request / response shapes. |
| `ACPClient` | `chimera.acp.client` | Manages the subprocess + JSON-RPC stdio framing. |
| `ExternalAgentTool` | `chimera.acp.tool` | Wraps an external agent as a Chimera `BaseTool`. |

`ExternalAgentTool` emits the lifecycle events
`ExternalAgentStartEvent`, `ExternalAgentToolCallEvent`, and
`ExternalAgentCompleteEvent` (see [`chimera.events`](/reference/events/)).

## See also

- [Connect External Agents](/connect-external-agents/) for setup.
- [`chimera.events`](/reference/events/) for the external-agent event types.
