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

from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import TYPE_CHECKING

from chimera.core.context import Context
from chimera.core.loop import ReAct
from chimera.core.prompt import Prompt
from chimera.core.tool import BaseTool, ContextAwareTool
from chimera.env.base import Environment
from chimera.providers.base import Provider
from chimera.types import AgentResult, Message, StepResult

if TYPE_CHECKING:
    from chimera.core.abort import AbortSignal
    from chimera.core.content_replacement import ContentReplacementState
    from chimera.core.loop_events import LoopEvent
    from chimera.hooks.executor import HookExecutor
    from chimera.permissions.checker import PermissionChecker
    from chimera.permissions.context import PermissionContext
    from chimera.sessions.transcript import TranscriptStorage


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
        for t in self.tools:
            if isinstance(t, ContextAwareTool):
                t.bind_context(context)
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
        for t in self.tools:
            if isinstance(t, ContextAwareTool):
                t.bind_context(context)
        return (yield from self.loop.iter_steps(self.provider, self.tools, context, env))

    async def async_run(self, task: str, env: Environment | None) -> AgentResult:
        """Run the agent asynchronously using async provider calls."""
        system = self.prompt.render(tools=[t.name for t in self.tools])
        context = Context(system=system)
        context.add(Message.user(task))
        for t in self.tools:
            if isinstance(t, ContextAwareTool):
                t.bind_context(context)
        return await self.loop.async_run(self.provider, self.tools, context, env)

    async def async_run_events(
        self,
        task: str,
        env: Environment | None = None,
        *,
        abort_signal: AbortSignal | None = None,
        permission_checker: PermissionChecker | None = None,
        permission_context: PermissionContext | None = None,
        hook_executor: HookExecutor | None = None,
        hook_matchers: list | None = None,
        transcript: TranscriptStorage | None = None,
        content_replacement: ContentReplacementState | None = None,
    ) -> AsyncGenerator[LoopEvent, None]:
        """Run using the new AgentLoop infrastructure, yielding LoopEvents.

        This is the new entry point that uses all Phase 1-8 modules.
        The old run()/iter_steps()/async_run() methods are preserved
        for backwards compatibility.
        """
        from chimera.core.agent_loop import AgentLoop
        from chimera.core.context_assembler import ContextAssembler
        from chimera.core.system_prompt import SystemPromptBuilder

        # Build system prompt using new infrastructure
        assembler = ContextAssembler(
            project_dir=Path(env.cwd if env and hasattr(env, "cwd") else "."),
            tools=self.tools,
            model=self.provider.model_name,
        )
        system_prompt = await assembler.assemble(
            user_append=self.prompt.render(tools=[t.name for t in self.tools])
            if self.prompt
            else None,
        )

        # Load feature flags
        from chimera.core.feature_flags import FeatureFlags

        FeatureFlags.from_env()

        # Load persistent memory
        from chimera.core.memory import PersistentMemory

        project_dir = Path(env.cwd if env and hasattr(env, "cwd") else ".")
        memory = PersistentMemory(project_dir)
        memory_content = memory.load()
        if memory_content:
            system_prompt = (
                SystemPromptBuilder()
                .add_layer("base", system_prompt.to_string())
                .add_layer("memory", memory_content, cacheable=False)
                .build()
            )

        loop = AgentLoop()
        async for event in loop.run(
            messages=[Message.user(task)],
            tools=self.tools,
            provider=self.provider,
            system_prompt=system_prompt,
            abort_signal=abort_signal,
            permission_checker=permission_checker,
            permission_context=permission_context,
            hook_executor=hook_executor,
            hook_matchers=hook_matchers,
            transcript=transcript,
            content_replacement=content_replacement,
        ):
            yield event
