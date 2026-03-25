"""AgentIndex: pre-computed keyword -> agent mapping for fast routing."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.agents.registry import AgentRegistry

__all__ = ["AgentIndex"]


class AgentIndex:
    """Pre-computed keyword to agent mapping for fast routing.

    Scans all agents in a :class:`~chimera.agents.registry.AgentRegistry`,
    extracts trigger keywords, and builds an inverted index for O(1) lookup.
    """

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry
        # keyword -> list of (agent_name, weight)
        self._inverted: dict[str, list[tuple[str, float]]] = {}
        # agent_name -> list of trigger keywords
        self._agent_triggers: dict[str, list[str]] = {}

    def build(self) -> None:
        """Scan all agents in the registry and build the inverted index.

        For each agent, extract:
        - triggers from ``AgentConfig.triggers`` (if non-empty)
        - Otherwise, keywords from the description (fallback)
        """
        self._inverted.clear()
        self._agent_triggers.clear()

        for name in self._registry.list():
            config = self._registry.get(name)
            if config is None:
                continue

            # Use explicit triggers if available, otherwise fall back to
            # description keywords.
            if config.triggers:
                triggers = [t.lower() for t in config.triggers]
            else:
                # Extract meaningful keywords from description (3+ chars)
                desc_words = re.findall(r"[a-z]+", config.description.lower())
                triggers = [w for w in desc_words if len(w) >= 3]

            self._agent_triggers[name] = triggers

            for keyword in triggers:
                if keyword not in self._inverted:
                    self._inverted[keyword] = []
                self._inverted[keyword].append((name, 1.0))

    def lookup(self, keywords: list[str]) -> list[tuple[str, float]]:
        """Return ``(agent_name, relevance_score)`` for matching agents.

        Score = number of matched trigger keywords / total trigger count
        for each agent.

        Args:
            keywords: Lowercased keywords extracted from the user request.

        Returns:
            List of (agent_name, score) sorted by score descending.
            Agents with zero overlap are excluded.
        """
        # Count hits per agent
        hits: dict[str, int] = {}
        for kw in keywords:
            for agent_name, _weight in self._inverted.get(kw, []):
                hits[agent_name] = hits.get(agent_name, 0) + 1

        # Compute scores
        results: list[tuple[str, float]] = []
        for agent_name, hit_count in hits.items():
            total = len(self._agent_triggers.get(agent_name, []))
            if total == 0:
                continue
            score = hit_count / total
            results.append((agent_name, score))

        results.sort(key=lambda t: t[1], reverse=True)
        return results

    @property
    def agent_triggers(self) -> dict[str, list[str]]:
        """Read-only access to the per-agent trigger lists."""
        return dict(self._agent_triggers)

    def save(self, path: Path) -> None:
        """Serialize the index to a JSON file.

        Args:
            path: Destination file path.
        """
        data = {
            "inverted": {
                k: [(name, weight) for name, weight in v]
                for k, v in self._inverted.items()
            },
            "agent_triggers": self._agent_triggers,
        }
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path, registry: AgentRegistry) -> AgentIndex:
        """Deserialize an index from a JSON file.

        Args:
            path: Source file path.
            registry: The agent registry (kept for future lookups).

        Returns:
            A reconstructed :class:`AgentIndex`.
        """
        data = json.loads(path.read_text())
        index = cls(registry)
        index._inverted = {
            k: [(name, weight) for name, weight in v]
            for k, v in data["inverted"].items()
        }
        index._agent_triggers = data["agent_triggers"]
        return index
