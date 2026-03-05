"""Extended plugin registry supporting all Chimera extension points."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.agents.config import AgentConfig
    from chimera.config.skills import Skill
    from chimera.plugins.base import Hook, MCPServerConfig


class PluginExtensionRegistry:
    """Class-level registry for plugin-provided extensions.

    Extends the per-instance :class:`ComponentRegistry` with global
    registries for agents, strategies, constraints, middleware, skills,
    MCP servers, and hooks.
    """

    _agents: dict[str, AgentConfig] = {}
    _strategies: dict[str, type] = {}
    _constraints: dict[str, type] = {}
    _middleware: list[type] = []
    _skills: dict[str, Skill] = {}
    _mcp_servers: dict[str, MCPServerConfig] = {}
    _hooks: dict[str, list[Hook]] = {}

    # -- Agents ---------------------------------------------------------------

    @classmethod
    def register_agent(cls, name: str, config: AgentConfig) -> None:
        """Register an agent configuration.

        Args:
            name: Unique agent name.
            config: The agent configuration.
        """
        cls._agents[name] = config

    @classmethod
    def get_agent(cls, name: str) -> AgentConfig | None:
        """Look up a registered agent by name.

        Args:
            name: Agent name.

        Returns:
            AgentConfig if found, None otherwise.
        """
        return cls._agents.get(name)

    @classmethod
    def get_all_agents(cls) -> dict[str, AgentConfig]:
        """Return all registered agent configs.

        Returns:
            Dictionary of name to AgentConfig.
        """
        return dict(cls._agents)

    # -- Strategies -----------------------------------------------------------

    @classmethod
    def register_strategy(cls, name: str, strategy_cls: type) -> None:
        """Register a training strategy class.

        Args:
            name: Unique strategy name.
            strategy_cls: The strategy class.
        """
        cls._strategies[name] = strategy_cls

    @classmethod
    def get_strategy(cls, name: str) -> type | None:
        """Look up a registered strategy by name.

        Args:
            name: Strategy name.

        Returns:
            Strategy class if found, None otherwise.
        """
        return cls._strategies.get(name)

    @classmethod
    def get_all_strategies(cls) -> dict[str, type]:
        """Return all registered strategy classes.

        Returns:
            Dictionary of name to strategy class.
        """
        return dict(cls._strategies)

    # -- Constraints ----------------------------------------------------------

    @classmethod
    def register_constraint(cls, name: str, constraint_cls: type) -> None:
        """Register a constraint class.

        Args:
            name: Unique constraint name.
            constraint_cls: The constraint class.
        """
        cls._constraints[name] = constraint_cls

    # -- Middleware ------------------------------------------------------------

    @classmethod
    def register_middleware(cls, middleware_cls: type) -> None:
        """Register an event middleware class.

        Args:
            middleware_cls: The middleware class.
        """
        cls._middleware.append(middleware_cls)

    @classmethod
    def get_all_middleware(cls) -> list[type]:
        """Return all registered middleware classes.

        Returns:
            List of middleware classes.
        """
        return list(cls._middleware)

    # -- Skills ---------------------------------------------------------------

    @classmethod
    def register_skill(cls, skill: Skill) -> None:
        """Register a skill.

        Args:
            skill: A Skill instance.
        """
        cls._skills[skill.name] = skill

    # -- MCP Servers ----------------------------------------------------------

    @classmethod
    def register_mcp_server(cls, name: str, config: MCPServerConfig) -> None:
        """Register an MCP server configuration.

        Args:
            name: Server name.
            config: MCP server configuration.
        """
        cls._mcp_servers[name] = config

    @classmethod
    def get_all_mcp_servers(cls) -> dict[str, MCPServerConfig]:
        """Return all registered MCP server configs.

        Returns:
            Dictionary of name to MCPServerConfig.
        """
        return dict(cls._mcp_servers)

    # -- Hooks ----------------------------------------------------------------

    @classmethod
    def register_hook(cls, event_type: str, hook: Hook) -> None:
        """Register a hook for an event type.

        Args:
            event_type: The event type to trigger on.
            hook: The hook to register.
        """
        cls._hooks.setdefault(event_type, []).append(hook)

    @classmethod
    def get_hooks(cls, event_type: str) -> list[Hook]:
        """Return hooks registered for an event type.

        Args:
            event_type: Event type to look up.

        Returns:
            List of hooks for the event type.
        """
        return cls._hooks.get(event_type, [])

    # -- Reset (for testing) --------------------------------------------------

    @classmethod
    def _reset(cls) -> None:
        """Clear all registries. Used in tests."""
        cls._agents.clear()
        cls._strategies.clear()
        cls._constraints.clear()
        cls._middleware.clear()
        cls._skills.clear()
        cls._mcp_servers.clear()
        cls._hooks.clear()
