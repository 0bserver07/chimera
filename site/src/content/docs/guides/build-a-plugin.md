---
title: "Build a Plugin"
description: "Build a Plugin"
---

Create a Chimera plugin that adds custom tools, agent presets, and
extensions to any Chimera-based project.

---

## Prerequisites

Familiarity with `BaseTool`.  See [Add a Custom Tool](/add-custom-tool/)
for the basics.

---

## Step 1: Create the Plugin Class

Subclass `BasePlugin` and set `name`, `version`, and `description`.
Override `activate(registry)` to register your extensions.

```python
from chimera import BasePlugin
from chimera.plugins.base import ComponentRegistry


class MyPlugin(BasePlugin):
    name = "my-plugin"  # unique identifier
    version = "1.0.0"
    description = "Adds timestamp tools and a reviewer agent."

    def activate(self, registry: ComponentRegistry) -> None:
        super().activate(registry)  # calls all register_* hooks

    def deactivate(self) -> None:
        pass  # cleanup if needed
```

The default `activate()` calls ten `register_*` hooks in order: tools,
loops, providers, agents, strategies, constraints, middleware, skills,
MCP servers, and hooks.  Override only the ones you need.

---

## Step 2: Register Tools

Override `register_tools()` to add tools via `registry.register_tool()`.

```python
from typing import Any
from chimera import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class TimestampTool(BaseTool):
    name = "timestamp"
    description = "Return the current UTC timestamp."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        return ToolResult(output=now)


class MyPlugin(BasePlugin):
    name = "my-plugin"
    version = "1.0.0"

    def register_tools(self, registry: ComponentRegistry) -> None:
        registry.register_tool(TimestampTool())
```

---

## Step 3: Load with PluginManager

```python
from chimera import PluginManager

manager = PluginManager()
manager.load_plugin(MyPlugin())

# All tools from all loaded plugins
print(manager.tools)  # [<TimestampTool>]
```

For entry-point-based discovery (installed packages), use `manager.load("my-plugin")`
or `manager.load_all()`.  Chimera looks up the `chimera.plugins` entry point
group in `pyproject.toml`:

```toml
[project.entry-points."chimera.plugins"]
my-plugin = "my_package.plugin:MyPlugin"
```

---

## Step 4: Directory-Based Plugins

For quick, no-code plugins, use a directory layout with a `plugin.json`
manifest. The manifest format was standardised in W13-E1 (`docs/plans/
W13-E1-PLUGIN-MANIFEST.md`) and is the recommended way to package skills,
agents, slash commands, hooks, and MCP servers together.

```
my-plugin/
  plugin.json              <- top-level manifest
  skills/
    auto-test.md
  agents/
    reviewer.md
  commands/
    review.md
  hooks/
    hooks.json
  .mcp.json
```

**plugin.json** -- the manifest enumerates every component the plugin
ships and (optionally) declares MCP servers and tags:

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "Lint feedback + reviewer agent + benchmark command.",
  "license": "MIT",
  "author": "Your Name",
  "homepage": "https://github.com/you/my-plugin",
  "tags": ["code-review", "lint"],
  "components": [
    { "type": "skill",   "name": "auto-test", "path": "skills/auto-test.md" },
    { "type": "agent",   "name": "reviewer",  "path": "agents/reviewer.md" },
    { "type": "command", "name": "review",    "path": "commands/review.md" },
    { "type": "hook",    "name": "hooks",     "path": "hooks/hooks.json" }
  ],
  "mcp_servers": {
    "my-search": {
      "command": ["python3", "-m", "my_plugin.search_server"],
      "module": "my_plugin.search_server",
      "description": "Custom symbol-aware code search."
    }
  }
}
```

| Field | Required | Purpose |
|---|---|---|
| `name` | yes | Unique plugin id (matches the directory name by convention). |
| `version` | yes | Semver string. |
| `description` | yes | One-sentence summary surfaced in the marketplace. |
| `license` | recommended | SPDX identifier (`MIT`, `Apache-2.0`, ...). |
| `components` | yes | List of `{type, name, path}` entries. Supported types: `skill`, `agent`, `command`, `hook`. |
| `mcp_servers` | optional | Map of MCP server configs; same shape as `.mcp.json`. |
| `tags` | optional | Free-form tags for marketplace search. |

**agents/reviewer.md** -- agent definition with YAML frontmatter
(loaded by `AgentConfig.from_markdown()`).

**.mcp.json** -- MCP server configs (alternative to inlining under
`mcp_servers` in the manifest):
```json
{
  "servers": {
    "my-server": {
      "command": ["node", "server.js"],
      "args": ["--port", "3000"],
      "env": {"NODE_ENV": "production"}
    }
  }
}
```

**hooks/hooks.json** -- shell hooks triggered by events:
```json
[
  {
    "command": "echo 'Tool called'",
    "event_type": "tool_call",
    "timeout": 10
  }
]
```

Load with `DirectoryPluginLoader`:

```python
from chimera import DirectoryPluginLoader
from chimera.plugins.base import ComponentRegistry

loader = DirectoryPluginLoader()
plugin = loader.load("/path/to/my-plugin")

registry = ComponentRegistry()
plugin.activate(registry)
```

The loader reads `plugin.json` first and uses the `components` list to
discover every skill/agent/command/hook -- you do not have to maintain a
parallel directory walk in your code.

---

## Step 5: Extension Registry

`PluginExtensionRegistry` is a class-level registry for advanced extensions
beyond tools. Use it inside your `register_*` hooks:

```python
from chimera import PluginExtensionRegistry
from chimera.plugins.base import Hook, MCPServerConfig

# In your plugin's register_hooks():
PluginExtensionRegistry.register_hook(
    "tool_call",
    Hook(command="echo 'tool was called'", event_type="tool_call"),
)

# In your plugin's register_mcp_servers():
PluginExtensionRegistry.register_mcp_server(
    "my-lsp",
    MCPServerConfig(command=["pylsp"]),
)
```

Available registries: agents, strategies, constraints, middleware, skills,
MCP servers, and hooks.

---

## Step 6: Marketplace

Publish plugins for discovery via `Marketplace`:

```python
from chimera import Marketplace, PluginInfo

mp = Marketplace()
mp.publish(PluginInfo(
    name="my-plugin",
    version="1.0.0",
    description="Timestamp tools for Chimera agents",
    author="Your Name",
    tags=["tools", "utilities"],
))

results = mp.search("timestamp")   # find plugins
mp.install("my-plugin")            # mark as installed
assert mp.is_installed("my-plugin")
```

---

## Complete Example

```python
"""my_plugin.py -- Full plugin with a tool and entry-point registration."""
from typing import Any

from chimera import BasePlugin, BaseTool, PluginManager
from chimera.env.base import Environment
from chimera.plugins.base import ComponentRegistry
from chimera.types import ToolResult


class UptimeTool(BaseTool):
    name = "uptime"
    description = "Return system uptime."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        import subprocess
        out = subprocess.check_output(["uptime"], text=True).strip()
        return ToolResult(output=out)


class UptimePlugin(BasePlugin):
    name = "uptime-plugin"
    version = "1.0.0"
    description = "Adds an uptime tool."

    def register_tools(self, registry: ComponentRegistry) -> None:
        registry.register_tool(UptimeTool())


# Usage
manager = PluginManager()
manager.load_plugin(UptimePlugin())
print(manager.tools[0].name)  # "uptime"
```

---

## Next Steps

- [Add a Custom Tool](/add-custom-tool/) -- tool basics (decorator and
  subclass patterns).
- [Configure Permissions](/configure-permissions/) -- control which
  plugin-provided tools the agent can call.
