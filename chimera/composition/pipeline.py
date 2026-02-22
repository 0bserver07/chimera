from __future__ import annotations

from chimera.core.agent import Agent
from chimera.env.base import Environment
from chimera.types import AgentResult


class Pipeline:
    """Sequential agent composition: output of agent N becomes input of agent N+1."""

    def __init__(self, agents: list[Agent]) -> None:
        self.agents = agents

    def run(self, task: str, env: Environment | None) -> AgentResult:
        current_input = task
        total_steps = 0
        total_tool_calls = 0
        total_cost = 0.0

        for agent in self.agents:
            result = agent.run(current_input, env)
            total_steps += result.steps
            total_tool_calls += result.tool_calls_total
            total_cost += result.cost
            if not result.success:
                return AgentResult(
                    output=result.output,
                    steps=total_steps,
                    tool_calls_total=total_tool_calls,
                    cost=total_cost,
                    success=False,
                    error=result.error,
                )
            current_input = result.output

        return AgentResult(
            output=current_input,
            steps=total_steps,
            tool_calls_total=total_tool_calls,
            cost=total_cost,
            success=True,
        )
