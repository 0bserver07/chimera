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
    a reasoning loop, and a system prompt, then call .run(task, env).
    """

    def __init__(
        self,
        provider: Provider,
        tools: list[BaseTool] | None = None,
        loop: ReAct | None = None,
        prompt: Prompt | None = None,
        name: str | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools or []
        self.loop = loop or ReAct()
        self.prompt = prompt or Prompt.from_string("You are a helpful coding agent.")
        self.name = name

    def run(self, task: str, env: Environment | None) -> AgentResult:
        """Run the agent on a task in the given environment.

        Creates a fresh Context, renders the system prompt, adds the task
        as a user message, and delegates to the loop.
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
