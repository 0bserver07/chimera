"""Extended plugin registry supporting all Chimera extension points."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from chimera.agents.config import AgentConfig
    from chimera.config.skills import Skill
    from chimera.core.interception import InterceptDecision, Interceptors
    from chimera.plugins.base import Hook, MCPServerConfig


#: Seam names accepted by :meth:`PluginExtensionRegistry.register_interceptor`,
#: matching the fields of :class:`chimera.core.interception.Interceptors`
#: (drift-pinned by ``tests/plugins/test_registry_interceptors.py``).
INTERCEPTOR_SEAMS: tuple[str, ...] = (
    "provider_request",
    "tool_call",
    "tool_result",
    "context",
)


class PluginExtensionRegistry:
    """Class-level registry for plugin-provided extensions.

    Extends the per-instance :class:`ComponentRegistry` with global
    registries for agents, strategies, constraints, middleware, skills,
    MCP servers, hooks, and interceptors.
    """

    _agents: dict[str, AgentConfig] = {}
    _strategies: dict[str, type] = {}
    _constraints: dict[str, type] = {}
    _middleware: list[type] = []
    _skills: dict[str, Skill] = {}
    _mcp_servers: dict[str, MCPServerConfig] = {}
    _hooks: dict[str, list[Hook]] = {}
    _interceptors: dict[str, list[Callable[..., InterceptDecision | None]]] = {}

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

    # -- Interceptors ----------------------------------------------------------

    @classmethod
    def register_interceptor(
        cls,
        seam: str,
        interceptor: Callable[..., InterceptDecision | None],
    ) -> None:
        """Register an interceptor for one of the four loop seams.

        Interceptors registered here are merged into every assembled
        agent's loop configuration: per seam, plugin-registered chains run
        first, in registration order, and host-supplied chains run last
        (see :func:`chimera.core.interception.merge_interceptors`).

        Args:
            seam: One of :data:`INTERCEPTOR_SEAMS` — ``"provider_request"``,
                ``"tool_call"``, ``"tool_result"``, or ``"context"``.
            interceptor: The interceptor callable; its signature is
                seam-specific (see :mod:`chimera.core.interception`).

        Raises:
            ValueError: If *seam* is not one of the four seam names. A
                typo must fail loudly here — a chain registered on a seam
                that does not exist would never fire, silently.
        """
        if seam not in INTERCEPTOR_SEAMS:
            raise ValueError(
                f"unknown interceptor seam {seam!r}; "
                f"expected one of {INTERCEPTOR_SEAMS}"
            )
        cls._interceptors.setdefault(seam, []).append(interceptor)

    @classmethod
    def unregister_interceptor(
        cls,
        seam: str,
        interceptor: Callable[..., InterceptDecision | None],
    ) -> None:
        """Remove a previously registered interceptor from a seam.

        The withdrawal half of :meth:`register_interceptor`, used by a
        plugin's ``deactivate()`` so unloading (or reloading) a plugin
        removes its chains. Removing an interceptor that is not
        registered is a no-op.

        Args:
            seam: One of :data:`INTERCEPTOR_SEAMS`.
            interceptor: The callable to remove (matched by equality, so
                a bound method re-derived from the same instance matches).

        Raises:
            ValueError: If *seam* is not one of the four seam names.
        """
        if seam not in INTERCEPTOR_SEAMS:
            raise ValueError(
                f"unknown interceptor seam {seam!r}; "
                f"expected one of {INTERCEPTOR_SEAMS}"
            )
        try:
            cls._interceptors.get(seam, []).remove(interceptor)
        except ValueError:
            pass

    @classmethod
    def get_interceptors(
        cls, seam: str,
    ) -> list[Callable[..., InterceptDecision | None]]:
        """Return interceptors registered for a seam, in registration order.

        Args:
            seam: One of :data:`INTERCEPTOR_SEAMS`.

        Returns:
            List of interceptor callables for the seam.

        Raises:
            ValueError: If *seam* is not one of the four seam names.
        """
        if seam not in INTERCEPTOR_SEAMS:
            raise ValueError(
                f"unknown interceptor seam {seam!r}; "
                f"expected one of {INTERCEPTOR_SEAMS}"
            )
        return list(cls._interceptors.get(seam, []))

    @classmethod
    def get_all_interceptors(cls) -> Interceptors:
        """Return all registered interceptors as one bundle.

        Returns:
            A fresh :class:`~chimera.core.interception.Interceptors`
            whose per-seam chains hold the registered interceptors in
            registration order (empty chains when nothing is registered).
            Mutating the returned bundle does not affect the registry.
        """
        from chimera.core.interception import Interceptors

        return Interceptors(
            provider_request=list(cls._interceptors.get("provider_request", [])),
            tool_call=list(cls._interceptors.get("tool_call", [])),
            tool_result=list(cls._interceptors.get("tool_result", [])),
            context=list(cls._interceptors.get("context", [])),
        )

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
        cls._interceptors.clear()
