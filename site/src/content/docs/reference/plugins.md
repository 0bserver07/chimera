---
title: "chimera.plugins"
description: "Reference for chimera.plugins — BasePlugin, PluginManager, ComponentRegistry, DirectoryPluginLoader, marketplace."
---

`chimera.plugins` is the extension surface: package tools, agent
presets, strategies, MCP servers, and hooks as a plugin and load them
into any Chimera-based project.

For the tutorial walk-through, see [Build a Plugin](/build-a-plugin/).

## Top-level exports

```python
from chimera.plugins import (
    BasePlugin,
    ComponentRegistry,
    Hook,
    MCPServerConfig,
    PluginManager,
    PluginExtensionRegistry,
    DirectoryPluginLoader,
    PluginInfo,
    Marketplace,
    MarketplaceRegistry,
)
```

| Symbol | Module | Purpose |
|---|---|---|
| `BasePlugin` | `chimera.plugins.base` | ABC. Set `name`, `version`, `description`. Override the `register_*` hooks you need. |
| `ComponentRegistry` | `chimera.plugins.base` | Per-plugin registry the `activate()` flow registers into. |
| `Hook` | `chimera.plugins.base` | Shell-hook config (`command`, `event_type`, `timeout`). |
| `MCPServerConfig` | `chimera.plugins.base` | One MCP server entry (`command`, `args`, `env`). |
| `PluginManager` | `chimera.plugins.manager` | Loads plugins, aggregates tools / agents / strategies. |
| `PluginExtensionRegistry` | `chimera.plugins.registry` | Class-level registry for advanced extensions (agents, strategies, constraints, middleware, skills, MCP servers, hooks). |
| `DirectoryPluginLoader` | `chimera.plugins.dir_loader` | Loads directory-based plugins (`plugin.json` + `agents/*.md` + `.mcp.json` + `hooks/`). |
| `PluginInfo` | `chimera.plugins.marketplace` | Marketplace metadata: `name`, `version`, `description`, `author`, `tags`. |
| `MarketplaceRegistry` / `Marketplace` | `chimera.plugins.marketplace` | Search, install, uninstall plugins from a registry index. |

## `register_*` hooks

`BasePlugin.activate(registry)` calls these in order — override only the
ones you need:

`register_tools`, `register_loops`, `register_providers`, `register_agents`,
`register_strategies`, `register_constraints`, `register_middleware`,
`register_skills`, `register_mcp_servers`, `register_hooks`.

## See also

- [Build a Plugin](/build-a-plugin/) for the full `plugin.json` manifest schema.
- [`chimera.mcp`](/reference/mcp/) for MCP server lifecycle.
- [`chimera.skills`](/reference/) for the `skills` extension target (loaded via SKILL.md).
