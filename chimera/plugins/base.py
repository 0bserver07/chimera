"""Plugin base classes, registry, and extension dataclasses."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.core.tool import BaseTool


# ---------------------------------------------------------------------------
# Extension dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Hook:
    """Shell command triggered by an event.

    Args:
        command: Shell command to execute.
        event_type: Event type that triggers this hook.
        working_dir: Working directory for the command.
        timeout: Max seconds to wait for the command.
        env: Extra environment variables.
    """

    command: str
    event_type: str
    working_dir: str | None = None
    timeout: int = 30
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class MCPServerConfig:
    """MCP server configuration provided by a plugin.

    Args:
        command: Command to start the server.
        args: Additional arguments.
        env: Environment variables.
    """

    command: list[str]
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Component registry (per-instance, passed during activation)
# ---------------------------------------------------------------------------

class ComponentRegistry:
    """Registry passed to plugins during activation.

    Plugins use this to register tools, loop classes, and provider classes
    that they provide.
    """

    def __init__(self) -> None:
        self._tools: list[BaseTool] = []
        self._loops: dict[str, type] = {}
        self._providers: dict[str, type] = {}

    def register_tool(self, tool: BaseTool) -> None:
        """Register a tool provided by a plugin.

        Args:
            tool: A BaseTool instance to add.
        """
        self._tools.append(tool)

    def register_loop(self, name: str, loop_class: type) -> None:
        """Register a loop class provided by a plugin.

        Args:
            name: Unique name for the loop.
            loop_class: The loop class to register.
        """
        self._loops[name] = loop_class

    def register_provider(self, name: str, provider_class: type) -> None:
        """Register a provider class provided by a plugin.

        Args:
            name: Unique name for the provider.
            provider_class: The provider class to register.
        """
        self._providers[name] = provider_class

    @property
    def tools(self) -> list[BaseTool]:
        """All registered tools."""
        return list(self._tools)

    @property
    def loops(self) -> dict[str, type]:
        """All registered loops."""
        return dict(self._loops)

    @property
    def providers(self) -> dict[str, type]:
        """All registered providers."""
        return dict(self._providers)

    # ------------------------------------------------------------------
    # Extension registrations: commands, hooks, skills
    # ------------------------------------------------------------------

    def register_command(self, command) -> None:
        """Register a command provided by a plugin.

        Args:
            command: A command descriptor (dict, dataclass, etc.).
        """
        self._commands = getattr(self, "_commands", [])
        self._commands.append(command)

    def register_hook(self, event: str, matcher) -> None:
        """Register a hook matcher for a given event.

        Args:
            event: The hook event name (e.g. ``"PreToolUse"``).
            matcher: A hook matcher descriptor.
        """
        self._hooks = getattr(self, "_hooks", {})
        self._hooks.setdefault(event, []).append(matcher)

    def register_skill(self, skill) -> None:
        """Register a skill provided by a plugin.

        Args:
            skill: A skill descriptor (dict, dataclass, etc.).
        """
        self._skills = getattr(self, "_skills", [])
        self._skills.append(skill)

    @property
    def commands(self) -> list:
        """All registered commands."""
        return list(getattr(self, "_commands", []))

    @property
    def hooks(self) -> dict[str, list]:
        """All registered hooks, keyed by event name."""
        return dict(getattr(self, "_hooks", {}))

    @property
    def skills(self) -> list:
        """All registered skills."""
        return list(getattr(self, "_skills", []))


# ---------------------------------------------------------------------------
# Base plugin
# ---------------------------------------------------------------------------

class BasePlugin(ABC):
    """Abstract base class for Chimera plugins.

    Subclass this and implement :meth:`activate` to register tools,
    loops, or providers with the plugin registry. Override the
    ``register_*`` methods to provide agents, strategies, skills,
    MCP servers, and hooks.

    Example:
        ```python
        class MyPlugin(BasePlugin):
            name = "my-plugin"
            version = "1.0.0"

            def activate(self, registry: ComponentRegistry) -> None:
                registry.register_tool(MyCustomTool())
        ```
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin name."""

    version: str = "0.1.0"
    description: str = ""
    author: str = ""

    def activate(self, registry: ComponentRegistry) -> None:
        """Called when the plugin is loaded.

        Override individual ``register_*`` methods to provide extensions,
        or override this method entirely for custom behaviour.

        Args:
            registry: Use to register tools, loops, and providers.
        """
        self.register_tools(registry)
        self.register_loops(registry)
        self.register_providers(registry)
        self.register_agents(registry)
        self.register_strategies(registry)
        self.register_constraints(registry)
        self.register_middleware(registry)
        self.register_skills(registry)
        self.register_mcp_servers(registry)
        self.register_hooks(registry)

    def register_tools(self, registry: ComponentRegistry) -> None:
        """Override to register tools."""

    def register_loops(self, registry: ComponentRegistry) -> None:
        """Override to register loop classes."""

    def register_providers(self, registry: ComponentRegistry) -> None:
        """Override to register provider classes."""

    def register_agents(self, registry: ComponentRegistry) -> None:
        """Override to register agent configs."""

    def register_strategies(self, registry: ComponentRegistry) -> None:
        """Override to register strategy classes."""

    def register_constraints(self, registry: ComponentRegistry) -> None:
        """Override to register constraint classes."""

    def register_middleware(self, registry: ComponentRegistry) -> None:
        """Override to register event middleware."""

    def register_skills(self, registry: ComponentRegistry) -> None:
        """Override to register skills."""

    def register_mcp_servers(self, registry: ComponentRegistry) -> None:
        """Override to register MCP server configs."""

    def register_hooks(self, registry: ComponentRegistry) -> None:
        """Override to register hooks."""

    def deactivate(self) -> None:
        """Called when the plugin is unloaded."""
