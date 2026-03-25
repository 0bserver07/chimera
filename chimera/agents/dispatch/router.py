"""AgentRouter: classify and route requests to the best agent."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from chimera.agents.dispatch.classifier import Complexity, RequestClassifier
from chimera.agents.dispatch.index import AgentIndex

if TYPE_CHECKING:
    from chimera.agents.config import AgentConfig
    from chimera.agents.registry import AgentRegistry
    from chimera.agents.dispatch.rules import ForceRoute

__all__ = ["AgentRouter", "RouteResult"]


@dataclass
class RouteResult:
    """Outcome of routing a request to an agent."""

    agent_config: AgentConfig
    """The matched agent configuration."""

    score: float
    """Match score from 0.0 to 1.0."""

    reason: str
    """Human-readable explanation of why this agent was selected."""

    complexity: Complexity
    """Classified complexity of the request."""


class AgentRouter:
    """Route requests to the best agent via force-routes and trigger scoring.

    Routing flow:
        1. Force-routes checked FIRST — if any match, return that agent
           with score=1.0, done.
        2. Build keyword set from request (lowercase, split on whitespace
           + punctuation).
        3. For each agent in index, count keyword overlap with agent's
           trigger list.
        4. Score = overlap_count / len(agent_triggers). Zero overlap = excluded.
        5. Sort by score descending, return top results.

    Args:
        registry: The agent registry to resolve names to configs.
        force_routes: Optional list of deterministic routing overrides.
        index: Optional pre-built agent index. If ``None``, one is built
            automatically from the registry.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        force_routes: list[ForceRoute] | None = None,
        index: AgentIndex | None = None,
    ) -> None:
        self._registry = registry
        self._force_routes = force_routes or []
        self._classifier = RequestClassifier()

        if index is not None:
            self._index = index
        else:
            self._index = AgentIndex(registry)
            self._index.build()

    def route(self, request: str) -> list[RouteResult]:
        """Route *request* to matching agents, sorted by score descending.

        Args:
            request: The user request text.

        Returns:
            List of :class:`RouteResult` (may be empty if nothing matches).
        """
        complexity = self._classifier.classify(request)

        # 1. Force-routes checked FIRST
        for fr in self._force_routes:
            if fr.matches(request):
                config = self._registry.get(fr.agent_name)
                if config is not None:
                    return [RouteResult(
                        agent_config=config,
                        score=1.0,
                        reason=fr.reason,
                        complexity=complexity,
                    )]

        # 2. Build keyword set from request
        keywords = re.findall(r"[a-z]+", request.lower())

        # 3-5. Lookup via index (scores computed inside)
        matches = self._index.lookup(keywords)

        results: list[RouteResult] = []
        for agent_name, score in matches:
            config = self._registry.get(agent_name)
            if config is None:
                continue
            results.append(RouteResult(
                agent_config=config,
                score=score,
                reason=f"Trigger match score {score:.2f} for agent '{agent_name}'",
                complexity=complexity,
            ))

        return results
