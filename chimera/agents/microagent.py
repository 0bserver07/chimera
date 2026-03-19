"""Microagent: lightweight, scoped sub-agents spawned by a parent agent.

Unlike DelegateTool (which delegates to an equal agent), microagents are:
- Budget-limited (max_steps, max_cost)
- Tool-restricted (subset of parent's tools)
- Disposable (no persistence after returning)

Inspired by OpenHands' microagent pattern.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.core.prompt import Prompt
from chimera.core.tool import BaseTool
from chimera.types import AgentResult

if TYPE_CHECKING:
    from chimera.env.base import Environment
    from chimera.providers.base import Provider


@dataclass
class MicroagentConfig:
    """Configuration for spawning a microagent.

    Args:
        name: Identifier for the microagent.
        task: The sub-task to perform.
        tools: Tools available (subset of parent's). Empty = text-only.
        max_steps: Maximum ReAct steps.
        max_cost: Maximum cost budget (0 = unlimited).
        system_prompt: Custom system prompt. None = default.
    """

    name: str
    task: str
    tools: list[str] = field(default_factory=list)
    max_steps: int = 10
    max_cost: float = 0.0
    system_prompt: str | None = None


class MicroagentSpawner:
    """Spawns and manages microagents on behalf of a parent agent."""

    def __init__(self, provider: Provider, available_tools: list[BaseTool]) -> None:
        self._provider = provider
        self._tool_map = {t.name: t for t in available_tools}

    def spawn(
        self,
        config: MicroagentConfig,
        env: Environment | None = None,
    ) -> AgentResult:
        """Spawn a microagent and run it to completion.

        Args:
            config: Microagent configuration.
            env: Environment to run in (shared with parent).

        Returns:
            The microagent's result.
        """
        # Resolve tools by name
        tools = [self._tool_map[name] for name in config.tools if name in self._tool_map]

        prompt = Prompt.from_string(
            config.system_prompt
            or f"You are a focused sub-agent named '{config.name}'. "
            "Complete the task concisely and return the result."
        )

        loop_kwargs: dict = {"max_steps": config.max_steps}

        # If there's a cost budget, use a LoopConfig with CostTracker
        if config.max_cost > 0:
            from chimera.core.loop_config import LoopConfig
            from chimera.providers.cost_tracker import CostTracker

            tracker = CostTracker(budget=config.max_cost)
            loop_kwargs["config"] = LoopConfig(cost_tracker=tracker)

        agent = Agent(
            provider=self._provider,
            tools=tools,
            loop=ReAct(**loop_kwargs),
            prompt=prompt,
            name=config.name,
        )

        return agent.run(config.task, env=env)

    def spawn_many(
        self,
        configs: list[MicroagentConfig],
        env: Environment | None = None,
    ) -> list[AgentResult]:
        """Spawn multiple microagents sequentially.

        Args:
            configs: List of microagent configurations.
            env: Shared environment.

        Returns:
            List of results, one per microagent.
        """
        return [self.spawn(cfg, env=env) for cfg in configs]
