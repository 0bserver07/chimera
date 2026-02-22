from __future__ import annotations

from chimera.core.agent import Agent
from chimera.env.base import Environment
from chimera.types import AgentResult


class Ensemble:
    """Parallel agent composition: all agents run the same task independently."""

    def __init__(self, agents: list[Agent]) -> None:
        self.agents = agents

    def run(self, task: str, env: Environment | None) -> list[AgentResult]:
        """Run all agents on the same task. Returns list of results."""
        results = []
        for agent in self.agents:
            result = agent.run(task, env)
            results.append(result)
        return results

    def best(self, results: list[AgentResult]) -> AgentResult:
        """Select the best result. Default: first successful result."""
        successful = [r for r in results if r.success]
        if successful:
            return successful[0]
        return results[0] if results else AgentResult(
            output="No results", steps=0, tool_calls_total=0, cost=0.0, success=False
        )
