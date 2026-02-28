"""Sequential agent pipeline composition.

The :class:`Pipeline` chains multiple agents so that the output of agent *N*
becomes the input of agent *N+1*.  Execution stops early if any agent fails.

Example:
    ```python
    pipeline = Pipeline(agents=[planner, coder, reviewer])
    result = pipeline.run("Build a CLI calculator.", env=sandbox)
    ```
"""

from __future__ import annotations

from chimera.core.agent import Agent
from chimera.env.base import Environment
from chimera.types import AgentResult


class Pipeline:
    """Sequential agent composition: output of agent *N* becomes input of agent *N+1*.

    Execution stops early and returns a failure result if any stage fails.

    Attributes:
        agents: Ordered list of agents forming the pipeline stages.
    """

    def __init__(self, agents: list[Agent]) -> None:
        """Initialise the pipeline.

        Args:
            agents: Ordered list of agents.  The first agent receives the
                raw task; each subsequent agent receives the previous
                agent's output.
        """
        self.agents = agents

    def run(self, task: str, env: Environment | None) -> AgentResult:
        """Execute the pipeline end-to-end.

        Args:
            task: Initial task description fed to the first agent.
            env: Shared execution environment (or ``None``).

        Returns:
            An :class:`~chimera.types.AgentResult` with aggregated cost,
            step count, and tool-call totals.  On early failure the result
            carries the error from the failing stage.
        """
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
