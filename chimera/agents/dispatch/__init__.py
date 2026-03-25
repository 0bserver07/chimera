"""Smart dispatch: request classification and agent routing."""
from __future__ import annotations

from chimera.agents.dispatch.classifier import Complexity, RequestClassifier
from chimera.agents.dispatch.dispatcher import Dispatcher
from chimera.agents.dispatch.index import AgentIndex
from chimera.agents.dispatch.router import AgentRouter, RouteResult
from chimera.agents.dispatch.rules import ForceRoute, RouteRule

__all__ = [
    "AgentIndex",
    "AgentRouter",
    "Complexity",
    "Dispatcher",
    "ForceRoute",
    "RequestClassifier",
    "RouteResult",
    "RouteRule",
]
