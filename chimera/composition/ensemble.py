"""Ensemble (fan-out) agent composition pattern.

The :class:`Ensemble` runs every agent on the *same* task and collects all
results.  Use :meth:`Ensemble.best` to pick the winning result (default:
first successful).

Example:
    ```python
    ensemble = Ensemble(agents=[agent_a, agent_b, agent_c])
    results = ensemble.run("Solve the problem.", env=None)
    winner = ensemble.best(results)
    ```
"""

from __future__ import annotations

from chimera.core.agent import Agent
from chimera.env.base import Environment
from chimera.types import AgentResult


class Ensemble:
    """Fan-out composition: all agents run the same task independently.

    All agents share the same ``env`` reference. If agents mutate the
    environment (writing files, running commands), mutations from earlier
    agents will be visible to later ones.  Pass ``env=None`` or separate
    env instances per agent if you need true independence.

    Attributes:
        agents: The pool of agents that will each attempt the task.
    """

    def __init__(self, agents: list[Agent]) -> None:
        """Initialise the ensemble.

        Args:
            agents: List of agents to run in parallel (currently executed
                sequentially).
        """
        self.agents = agents

    def run(self, task: str, env: Environment | None) -> list[AgentResult]:
        """Run every agent on the same task and collect all results.

        Args:
            task: Task description given to each agent.
            env: Shared execution environment (or ``None``).

        Returns:
            A list of :class:`~chimera.types.AgentResult` objects, one per
            agent, in the same order as :attr:`agents`.
        """
        results = []
        for agent in self.agents:
            result = agent.run(task, env)
            results.append(result)
        return results

    def best(self, results: list[AgentResult]) -> AgentResult:
        """Select the best result from an ensemble run.

        The default heuristic returns the first successful result.  Override
        this method to implement custom ranking (e.g. lowest cost, highest
        test-pass rate).

        Args:
            results: List of results as returned by :meth:`run`.

        Returns:
            The single best :class:`~chimera.types.AgentResult`.  If no
            result succeeded, returns the first result (or a synthetic
            failure if the list is empty).
        """
        successful = [r for r in results if r.success]
        if successful:
            return successful[0]
        return results[0] if results else AgentResult(
            output="No results", steps=0, tool_calls_total=0, cost=0.0, success=False
        )
