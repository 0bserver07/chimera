"""Supervisor (coordinator + workers) agent composition pattern.

The :class:`Supervisor` gives a *coordinator* agent delegate tools that let it
dispatch sub-tasks to a set of *worker* agents, then synthesize their results.

Example:
    ```python
    supervisor = Supervisor(
        coordinator=manager_agent,
        workers={"research": researcher, "code": coder},
    )
    result = supervisor.run("Implement and test a caching layer.", env=sandbox)
    ```
"""

from __future__ import annotations

from chimera.core.agent import Agent
from chimera.env.base import Environment
from chimera.tools.delegate import DelegateTool
from chimera.types import AgentResult


class Supervisor:
    """Coordinator + workers pattern.

    The coordinator agent receives a
    :class:`~chimera.tools.delegate.DelegateTool` for every worker,
    allowing it to dispatch sub-tasks and collect their results.

    Attributes:
        coordinator: The agent responsible for planning and delegation.
        workers: Name-to-agent mapping of specialist worker agents.
    """

    def __init__(self, coordinator: Agent, workers: dict[str, Agent]) -> None:
        """Initialise the Supervisor.

        Args:
            coordinator: The orchestrating agent.  Delegate tools are
                appended to its tool list automatically.
            workers: Mapping of worker names to agent instances.  Each
                entry becomes a callable delegate tool on the coordinator.
        """
        self.coordinator = coordinator
        self.workers = workers
        # Add delegate tools for each worker
        for name, worker in workers.items():
            self.coordinator.tools.append(DelegateTool(sub_agent=worker, tool_name=name))

    def run(self, task: str, env: Environment | None) -> AgentResult:
        """Run the coordinator, which may delegate to workers as needed.

        Args:
            task: High-level task description.
            env: Shared execution environment (or ``None``).

        Returns:
            An :class:`~chimera.types.AgentResult` from the coordinator
            agent, which typically synthesises the workers' outputs.
        """
        return self.coordinator.run(task, env)
