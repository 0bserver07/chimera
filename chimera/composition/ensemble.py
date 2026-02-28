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

import shutil
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import TYPE_CHECKING

from chimera.core.agent import Agent
from chimera.types import AgentResult

if TYPE_CHECKING:
    from chimera.env.base import Environment


class Ensemble:
    """Fan-out composition: all agents run the same task independently.

    When *env* supports :meth:`~chimera.env.base.Environment.clone`, agents
    run in parallel using a :class:`~concurrent.futures.ThreadPoolExecutor`.
    Otherwise, agents run sequentially sharing the same environment.

    Attributes:
        agents: The pool of agents that will each attempt the task.
        max_workers: Maximum parallel threads (default: number of agents).
        timeout: Per-agent timeout in seconds (default: no timeout).
    """

    def __init__(
        self,
        agents: list[Agent],
        max_workers: int | None = None,
        timeout: float | None = None,
    ) -> None:
        self.agents = agents
        self.max_workers = max_workers
        self.timeout = timeout

    def run(self, task: str, env: Environment | None) -> list[AgentResult]:
        """Run every agent on the same task and collect results.

        Tries parallel execution first. Falls back to sequential if the
        environment does not support cloning.

        Args:
            task: Task description given to each agent.
            env: Execution environment (or ``None``).

        Returns:
            A list of AgentResult objects, one per agent, in agent order.
        """
        if not self.agents:
            return []
        if env is None:
            return self._run_sequential(task, env)
        try:
            return self._run_parallel(task, env)
        except NotImplementedError:
            return self._run_sequential(task, env)

    def _run_parallel(self, task: str, env: Environment) -> list[AgentResult]:
        """Run agents in parallel, each with a cloned environment."""
        clones: list[Environment] = []
        try:
            for _ in self.agents:
                clones.append(env.clone())

            def _run_agent(agent: Agent, clone: Environment) -> AgentResult:
                try:
                    return agent.run(task, clone)
                except Exception as exc:
                    return AgentResult(
                        output="", steps=0, tool_calls_total=0,
                        cost=0.0, success=False, error=str(exc),
                    )

            results: list[AgentResult | None] = [None] * len(self.agents)
            with ThreadPoolExecutor(max_workers=self.max_workers or len(self.agents)) as pool:
                futures = {
                    pool.submit(_run_agent, agent, clone): i
                    for i, (agent, clone) in enumerate(zip(self.agents, clones))
                }
                for future in futures:
                    idx = futures[future]
                    try:
                        results[idx] = future.result(timeout=self.timeout)
                    except FuturesTimeoutError:
                        results[idx] = AgentResult(
                            output="", steps=0, tool_calls_total=0,
                            cost=0.0, success=False, error="Timeout",
                        )
                    except Exception as exc:
                        results[idx] = AgentResult(
                            output="", steps=0, tool_calls_total=0,
                            cost=0.0, success=False, error=str(exc),
                        )
            return [r for r in results if r is not None]  # type: ignore[misc]
        finally:
            for clone in clones:
                try:
                    if hasattr(clone, 'workdir'):
                        shutil.rmtree(str(clone.workdir), ignore_errors=True)
                    clone.cleanup()
                except Exception:
                    pass

    def _run_sequential(self, task: str, env: Environment | None) -> list[AgentResult]:
        """Run agents sequentially (original behavior)."""
        results = []
        for agent in self.agents:
            result = agent.run(task, env)
            results.append(result)
        return results

    def best(self, results: list[AgentResult]) -> AgentResult:
        """Select the best result from an ensemble run.

        The default heuristic returns the first successful result. Override
        this method to implement custom ranking.

        Args:
            results: List of results as returned by :meth:`run`.

        Returns:
            The single best AgentResult.
        """
        successful = [r for r in results if r.success]
        if successful:
            return successful[0]
        return results[0] if results else AgentResult(
            output="No results", steps=0, tool_calls_total=0, cost=0.0, success=False
        )
