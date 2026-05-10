---
title: "Plugins"
description: "Plugins"
---

Chimera's plugin system lets you extend the framework with custom tools, loops, providers, agents, strategies, MCP servers, hooks, and more -- without modifying any core code. Plugins are discovered via Python entry points or loaded from directory structures, and a built-in marketplace provides search, install, and uninstall workflows.

## Quick Start

```python
from chimera.plugins import BasePlugin, ComponentRegistry

class MyPlugin(BasePlugin):
    name = "my-plugin"
    version = "1.0.0"
    description = "Adds a custom tool to Chimera"

    def register_tools(self, registry: ComponentRegistry) -> None:
        from chimera.core.tool import tool

        @tool
        def hello(name: str) -> str:
            """Say hello."""
            return f"Hello, {name}!"

        registry.register_tool(hello)
```

## Key Classes

| Class | Module | Description |
|-------|--------|-------------|
| `BasePlugin` | `chimera.plugins.base` | Abstract base class all plugins extend. Provides `activate()`, `deactivate()`, and ten `register_*()` hooks. |
| `ComponentRegistry` | `chimera.plugins.base` | Instance-level registry passed during activation. Plugins register tools, loops, and providers here. |
| `Hook` | `chimera.plugins.base` | Dataclass: a shell command triggered by an event type (`command`, `event_type`, `working_dir`, `timeout`, `env`). |
| `MCPServerConfig` | `chimera.plugins.base` | Dataclass: MCP server configuration (`command`, `args`, `env`). |
| `PluginManager` | `chimera.plugins.manager` | Discovers plugins via the `chimera.plugins` entry point group, loads/unloads them, and exposes their registered tools. |
| `PluginExtensionRegistry` | `chimera.plugins.registry` | Class-level (global) registry for agents, strategies, constraints, middleware, skills, MCP servers, and hooks. |
| `DirectoryPluginLoader` | `chimera.plugins.dir_loader` | Loads plugins from a conventional directory layout (`plugin.json`, `agents/*.md`, `.mcp.json`, `hooks/hooks.json`). |
| `PluginInfo` | `chimera.plugins.marketplace` | Dataclass describing a marketplace plugin (name, version, description, author, url, tags, downloads, rating). |
| `MarketplaceRegistry` | `chimera.plugins.marketplace` | In-memory registry with `search()`, `by_tag()`, `top_rated()`, and `list_all()`. |
| `Marketplace` | `chimera.plugins.marketplace` | Wraps `MarketplaceRegistry` with `publish()`, `install()`, `uninstall()`, and install-state tracking. |

## Usage

### Writing a plugin class

Subclass `BasePlugin` and override the `register_*` methods for the extension points you need. The default `activate()` implementation calls all ten `register_*` methods in sequence:

```python
from chimera.plugins import BasePlugin, ComponentRegistry

class LintPlugin(BasePlugin):
    name = "lint-tools"
    version = "2.0.0"
    description = "Provides linting tools and a review agent"

    def register_tools(self, registry: ComponentRegistry) -> None:
        registry.register_tool(my_lint_tool)

    def register_agents(self, registry: ComponentRegistry) -> None:
        from chimera.plugins.registry import PluginExtensionRegistry
        PluginExtensionRegistry.register_agent("lint-reviewer", agent_config)
```

### Loading plugins at runtime

Use `PluginManager` to discover and load entry-point-based plugins, or load an already-instantiated plugin directly:

```python
from chimera.plugins import PluginManager

manager = PluginManager()

# Discover all available entry points
available = manager.discover()  # e.g. ["lint-tools", "db-connector"]

# Load one by name
plugin = manager.load("lint-tools")

# Or load all at once
all_plugins = manager.load_all()

# Access tools contributed by all loaded plugins
tools = manager.tools

# Unload when done
manager.unload("lint-tools")
```

### Loading from a directory

The `DirectoryPluginLoader` reads a conventional directory layout:

```
my-plugin/
  plugin.json          # {"name": "my-plugin", "version": "1.0.0"}
  agents/
    code-reviewer.md   # Agent definition with YAML frontmatter
  .mcp.json            # {"servers": {"my-server": {"command": ["node", "server.js"]}}}
  hooks/
    hooks.json          # [{"command": "make lint", "event_type": "tool_call"}]
```

```python
from chimera.plugins import DirectoryPluginLoader, ComponentRegistry

loader = DirectoryPluginLoader()
plugin = loader.load("/path/to/my-plugin")

registry = ComponentRegistry()
plugin.activate(registry)
```

### Using the marketplace

```python
from chimera.plugins import Marketplace, PluginInfo

mp = Marketplace()

# Publish a plugin
mp.publish(PluginInfo(
    name="code-analyzer",
    version="1.0.0",
    description="Static code analysis tools",
    tags=["analysis", "quality"],
    rating=4.5,
))

# Search and install
results = mp.search("analysis")
mp.install("code-analyzer")
assert mp.is_installed("code-analyzer")

# Browse by tag or rating
quality_plugins = mp.registry.by_tag("quality")
top_plugins = mp.registry.top_rated(limit=5)
```

### Configuring the index URL (Wave 11 B2)

The marketplace ships with **no default index URL**
(`DEFAULT_INDEX_URL: str | None = None`). Rationale: we don't want to
commit to a registry SLA or signing infrastructure, and the trust
model is cleaner when every operator owns their index. Three
configuration paths are evaluated in order:

1. `chimera plugins ... --index <url-or-path>` flag.
2. `$CHIMERA_PLUGIN_INDEX` environment variable.
3. `chimera config set plugin_index <url>` (TOML, `[global]`).

When none of the three resolve, `chimera plugins search` and
`chimera plugins install` exit `rc=2` (configuration error) with a
friendly message printed to *stderr*; stdout stays clean for `jq`
consumers. A fetch / parse error stays at `rc=1` so scripted callers
can disambiguate.

A clearly-labeled sample index lives at
[`examples/plugin-index.json`](https://github.com/0bserver07/chimera/blob/master/examples/plugin-index.json)
with three fake entries (each `description` prefixed
`EXAMPLE -`) and an `_note: "EXAMPLE INDEX..."` tripwire. Tests
treat the warning as a regression guard so future maintainers can't
accidentally promote it to a real registry.

### chimera-plugin manifest (Wave 13 E1)

The in-tree `chimera-plugin/` skill / agent / command / hook / MCP
bundle now ships a single `plugin.json` that serves both
consumers:

- **Marketplace** (`PluginInfo.from_dict`) — picks up
  `name`, `version`, `description`, `author`, `tags`. Unknown
  keys are ignored.
- **Directory loader** (`DirectoryPluginLoader`) — picks up
  `name`, `version`, `description`, `author`. The loader's
  `MANIFEST_FILES` list checks `plugin.json` first, then falls
  through to `.claude-plugin/plugin.json`.

Manifest shape (verbatim fields):

```json
{
  "name": "chimera-plugin",
  "version": "0.2.0",
  "description": "Skills, agents, commands, hooks, MCP servers",
  "license": "MIT",
  "author": "Chimera",
  "homepage": "https://github.com/0bserver07/chimera",
  "tags": ["skills", "agents", "mcp", "hooks"],
  "components": [
    {"type": "skill",   "name": "code-review",       "path": "skills/code-review/"},
    {"type": "agent",   "name": "reviewer",          "path": "agents/reviewer.md"},
    {"type": "command", "name": "benchmark",         "path": "commands/benchmark.md"},
    {"type": "hook",    "name": "auto_test",         "path": "hooks/hooks.json"}
  ],
  "mcp_servers": {
    "search":    {"command": ["python3", "-m", "chimera.mcp_servers.search_server"],
                  "module":  "chimera.mcp_servers.search_server"},
    "review":    {"command": ["python3", "-m", "chimera.mcp_servers.review_server"],
                  "module":  "chimera.mcp_servers.review_server"},
    "testgen":   {"command": ["python3", "-m", "chimera.mcp_servers.testgen_server"],
                  "module":  "chimera.mcp_servers.testgen_server"},
    "migration": {"command": ["python3", "-m", "chimera.mcp_servers.migration_server"],
                  "module":  "chimera.mcp_servers.migration_server"},
    "rag":       {"command": ["python3", "-m", "chimera.mcp_servers.rag_server"],
                  "module":  "chimera.mcp_servers.rag_server"},
    "benchmark": {"command": ["python3", "-m", "chimera.mcp_servers.benchmark_server"],
                  "module":  "chimera.mcp_servers.benchmark_server"}
  }
}
```

`components[]` covers 14 skills + 3 agents + 5 commands + 1 hook
bundle; each path is relative to `chimera-plugin/` and resolves to a
real file on disk. Six MCP servers are listed with both a subprocess
`command` and a `module` field for direct Python import.

The sample `examples/plugin-index.json` carries a real
`chimera-plugin` entry (with `_note: "Built-in plugin (real)"`)
alongside the three EXAMPLE placeholders so it can be distinguished
both visually and programmatically.

## Integration

- **PluginManager.tools** returns all `BaseTool` instances registered by loaded plugins, ready to pass to `Agent` or `ToolGroup`.
- **PluginExtensionRegistry** stores agents, strategies, constraints, middleware, skills, MCP servers, and hooks at the class level. These are available globally and can be queried by other Chimera subsystems (e.g., `AgentRegistry`, `MCPClient`).
- **Hooks** connect to the **EventBus** -- each `Hook` specifies an `event_type` string matching Chimera event types (`tool_call`, `tool_result`, `security_event`, etc.), and the shell command runs when that event fires.
- **MCPServerConfig** integrates with `chimera.mcp` -- plugins can provide MCP server definitions that are started and managed alongside the agent session.
- The CLI `chimera plugins` subcommand exposes plugin management (list, install, uninstall) to end users.

## Import Reference

```python
from chimera.plugins import (
    BasePlugin,
    ComponentRegistry,
    DirectoryPluginLoader,
    Hook,
    MCPServerConfig,
    Marketplace,
    MarketplaceRegistry,
    PluginExtensionRegistry,
    PluginInfo,
    PluginManager,
)
```
