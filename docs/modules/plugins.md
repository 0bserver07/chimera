# Plugins

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
