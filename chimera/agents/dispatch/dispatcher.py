"""Dispatcher facade: classify -> route -> configure -> return Agent."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chimera.agents.dispatch.classifier import RequestClassifier
from chimera.agents.dispatch.index import AgentIndex
from chimera.agents.dispatch.router import AgentRouter

if TYPE_CHECKING:
    from chimera.agents.dispatch.rules import ForceRoute
    from chimera.agents.registry import AgentRegistry
    from chimera.core.agent import Agent
    from chimera.providers.base import Provider

__all__ = ["Dispatcher"]


class Dispatcher:
    """Facade: classify, route, configure, and return a ready Agent.

    Wraps :class:`~chimera.agents.registry.AgentRegistry` — does NOT
    replace it. Manual agent construction still works.

    Args:
        registry: The agent registry to resolve agent configs.
        force_routes: Optional deterministic routing overrides.
        learning_store: Optional learning store for logging dispatch
            decisions (from issue #116). ``None`` by default.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        force_routes: list[ForceRoute] | None = None,
        learning_store: Any | None = None,
    ) -> None:
        self._registry = registry
        self._classifier = RequestClassifier()
        self._index = AgentIndex(registry)
        self._index.build()
        self._router = AgentRouter(
            registry,
            force_routes=force_routes,
            index=self._index,
        )
        self._learning_store = learning_store

    def dispatch(
        self,
        request: str,
        provider: Provider,
        **agent_kwargs: Any,
    ) -> Agent:
        """Classify, route, and build an Agent for *request*.

        Args:
            request: The user request text.
            provider: The LLM provider to wire into the agent.
            **agent_kwargs: Additional keyword arguments forwarded to
                :meth:`AgentConfig.build`.

        Returns:
            A fully configured :class:`~chimera.core.agent.Agent`.

        Raises:
            ValueError: If no agent matches the request.
        """
        # 1. Classify
        complexity = self._classifier.classify(request)

        # 2. Route
        results = self._router.route(request)
        if not results:
            raise ValueError(
                f"No agent matches request (complexity={complexity.value}): "
                f"{request!r}"
            )

        # 3. Pick top result, build Agent
        top = results[0]
        agent = top.agent_config.build(provider, **agent_kwargs)

        # 4. Log dispatch decision if learning_store is present
        if self._learning_store is not None:
            try:
                self._learning_store.log(
                    request=request,
                    complexity=complexity.value,
                    agent_name=top.agent_config.name,
                    score=top.score,
                    reason=top.reason,
                )
            except Exception:
                pass  # Best-effort logging

        return agent

    def explain(self, request: str) -> str:
        """Return a human-readable routing explanation without executing.

        Args:
            request: The user request text.

        Returns:
            Formatted string like:
            ``'Complexity: MODERATE | Agent: build | Score: 0.85 | Reason: ...'``
        """
        complexity = self._classifier.classify(request)
        results = self._router.route(request)

        if not results:
            return (
                f"Complexity: {complexity.value.upper()} | "
                f"Agent: none | Score: 0.00 | Reason: no matching agent"
            )

        top = results[0]
        return (
            f"Complexity: {complexity.value.upper()} | "
            f"Agent: {top.agent_config.name} | "
            f"Score: {top.score:.2f} | "
            f"Reason: {top.reason}"
        )
