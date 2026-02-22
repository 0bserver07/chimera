from __future__ import annotations

from chimera.core.agent import Agent
from chimera.env.base import Environment
from chimera.tools.delegate import DelegateTool
from chimera.types import AgentResult


class Supervisor:
    """Coordinator + workers pattern. The coordinator agent gets delegate tools for each worker."""

    def __init__(self, coordinator: Agent, workers: dict[str, Agent]) -> None:
        self.coordinator = coordinator
        self.workers = workers
        # Add delegate tools for each worker
        for name, worker in workers.items():
            self.coordinator.tools.append(DelegateTool(sub_agent=worker, tool_name=name))

    def run(self, task: str, env: Environment | None) -> AgentResult:
        return self.coordinator.run(task, env)
