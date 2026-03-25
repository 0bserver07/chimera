"""Routing rules: force-routes and soft route rules."""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["ForceRoute", "RouteRule"]


@dataclass
class ForceRoute:
    """Deterministic routing override.

    When the pattern matches, the named agent is selected unconditionally.
    """

    pattern: str
    """Regex pattern matched against request text."""

    agent_name: str
    """Registry name of the agent to force-select."""

    reason: str
    """Human-readable explanation of why this force-route exists."""

    def matches(self, request: str) -> bool:
        """Return ``True`` if *request* matches :attr:`pattern`.

        Args:
            request: The user request text.

        Returns:
            Whether the pattern matched (case-insensitive).
        """
        return re.search(self.pattern, request, re.IGNORECASE) is not None


@dataclass
class RouteRule:
    """Soft routing rule with a weight."""

    pattern: str
    """Regex pattern matched against request text."""

    agent_name: str
    """Registry name of the agent this rule favours."""

    weight: float = 1.0
    """Weight multiplier for this rule's contribution to the score."""
