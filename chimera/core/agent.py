"""Core agent module that ties together providers, tools, loops, and prompts.

The :class:`Agent` is the fundamental compositional unit in Chimera.  It wires
a language-model provider to a set of tools, a reasoning loop, and a system
prompt, then exposes a single :meth:`Agent.run` entry point.

Example:
    ```python
    from chimera.core.agent import Agent
    from chimera.providers.factory import create_provider

    provider = create_provider(model="claude-sonnet-4-20250514")
    agent = Agent(provider=provider, name="coder")
    result = agent.run("Write a hello-world script.", env=None)
    print(result.output)
    ```
"""

from __future__ import annotations

from collections.abc import Generator

from chimera.core.context import Context
from chimera.core.loop import ReAct
from chimera.core.prompt import Prompt
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.providers.base import Provider
from chimera.types import AgentResult, Message, StepResult


class Agent:
    """Agent = Provider + Tools + Loop + Prompt.

    The core compositional unit: wire together a language model, a set of tools,
    a reasoning loop, and a system prompt, then call :meth:`run`.

    Agents are designed to be composed -- they can be chained in a
    :class:`~chimera.composition.pipeline.Pipeline`, fanned out in an
    :class:`~chimera.composition.ensemble.Ensemble`, or coordinated via a
    :class:`~chimera.composition.supervisor.Supervisor`.

    Attributes:
        provider: The LLM backend used for completions.
        tools: Tools the agent can invoke during a run.
        loop: The reasoning loop (defaults to :class:`ReAct`).
        prompt: System prompt template.
        name: Optional human-readable identifier.
    """

    def __init__(
        self,
        provider: Provider,
        tools: list[BaseTool] | None = None,
        loop: ReAct | None = None,
        prompt: Prompt | None = None,
        name: str | None = None,
    ) -> None:
        """Initialise an Agent.

        Args:
            provider: LLM backend that implements
                :meth:`~chimera.providers.base.Provider.complete`.
            tools: Optional list of tools the agent may call.  Defaults to an
                empty list.
            loop: Reasoning loop implementation.  Defaults to :class:`ReAct`.
            prompt: System prompt template.  Defaults to a generic helper
                prompt.
            name: Optional name for logging and debugging.
        """
        self.provider = provider
        self.tools = tools or []
        self.loop = loop or ReAct()
        self.prompt = prompt or Prompt.from_string("You are a helpful coding agent.")
        self.name = name

    def run(self, task: str, env: Environment | None) -> AgentResult:
        """Run the agent on a task in the given environment.

        Creates a fresh :class:`~chimera.core.context.Context`, renders the
        system prompt, adds the task as a user message, and delegates to the
        reasoning loop.

        Args:
            task: Natural-language description of what the agent should do.
            env: Execution environment (sandbox, container, etc.) the agent
                can interact with, or ``None`` for stateless tasks.

        Returns:
            An :class:`~chimera.types.AgentResult` containing the final
            output, cost, step count, and success status.
        """
        system = self.prompt.render(tools=[t.name for t in self.tools])
        context = Context(system=system)
        context.add(Message.user(task))
        return self.loop.run(self.provider, self.tools, context, env)

    def iter_steps(
        self, task: str, env: Environment | None,
    ) -> Generator[StepResult, None, AgentResult]:
        """Yield one :class:`StepResult` per LLM turn.

        Creates a fresh Context and delegates to the loop's ``iter_steps``.
        """
        system = self.prompt.render(tools=[t.name for t in self.tools])
        context = Context(system=system)
        context.add(Message.user(task))
        return (yield from self.loop.iter_steps(self.provider, self.tools, context, env))

    async def async_run(self, task: str, env: Environment | None) -> AgentResult:
        """Run the agent asynchronously using async provider calls."""
        system = self.prompt.render(tools=[t.name for t in self.tools])
        context = Context(system=system)
        context.add(Message.user(task))
        return await self.loop.async_run(self.provider, self.tools, context, env)
