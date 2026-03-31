"""AgentSpawner: create and run sub-agent instances from definitions.

Provides :class:`AgentSpawner` which takes an :class:`AgentDefinition`,
creates an isolated :class:`AgentContext`, resolves tools, and drives
an :class:`AgentLoop` — yielding events back to the caller or launching
the agent as a background task.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from chimera.core.agent_context import AgentContext, IsolationLevel
from chimera.core.agent_definition import AgentDefinition
from chimera.core.agent_loop import AgentLoop
from chimera.core.loop_events import LoopEvent, LoopEventType
from chimera.core.task_manager import TaskManager
from chimera.core.tool import BaseTool
from chimera.types import Message

__all__ = ["AgentSpawner"]


class AgentSpawner:
    """Creates and runs sub-agents from :class:`AgentDefinition` instances.

    Args:
        provider: LLM provider with an ``async_complete`` method.
        available_tools: All tools that may be filtered by agent definitions.
        task_manager: Manager for registering background tasks.
    """

    def __init__(
        self,
        *,
        provider: Any,
        available_tools: list[BaseTool],
        task_manager: TaskManager,
    ) -> None:
        self._provider = provider
        self._available_tools = available_tools
        self._task_manager = task_manager

    async def spawn(
        self,
        definition: AgentDefinition,
        prompt: str,
        parent_context: AgentContext,
        *,
        model_override: str | None = None,
        run_in_background: bool = False,
        isolation: IsolationLevel = IsolationLevel.FULL,
        share_abort: bool = False,
    ) -> AsyncGenerator[LoopEvent, None]:
        """Spawn a sub-agent and yield its events.

        Creates a child context from *parent_context*, resolves the tool
        set from the definition, builds the system prompt, and runs an
        :class:`AgentLoop`.

        If *run_in_background* is ``True``, the agent is launched as an
        asyncio task and a single ``system`` event is yielded indicating
        the task was launched.

        Args:
            definition: The agent definition describing tools and prompt.
            prompt: The user prompt to send to the sub-agent.
            parent_context: The calling agent's context.
            model_override: Override the definition's model choice.
            run_in_background: If ``True``, run as a background task.
            isolation: Isolation level for the child context.
            share_abort: Whether to link the child's abort signal to the parent's.

        Yields:
            :class:`LoopEvent` instances from the sub-agent's execution.
        """
        # Create child context
        child_ctx = AgentContext.create_child(
            parent_context,
            isolation=isolation,
            share_abort=share_abort,
        )

        # Resolve tools
        tools = self._resolve_tools(definition)

        # Build system prompt
        system_prompt = definition.system_prompt or (
            f"You are the {definition.name} agent. {definition.description}"
        )

        # Build initial messages with the prompt
        messages = [Message.user(prompt)]

        if run_in_background:
            # Register background task and launch
            task = self._task_manager.register(
                agent_id=child_ctx.agent_id,
                description=f"{definition.name}: {prompt[:100]}",
            )

            async def _run_background() -> None:
                try:
                    loop = AgentLoop()
                    async for _ in loop.run(
                        messages=messages,
                        tools=tools,
                        provider=self._provider,
                        system_prompt=system_prompt,
                        abort_signal=child_ctx.abort_signal,
                        query_source=child_ctx.query_source,
                    ):
                        pass  # Consume events silently in background
                finally:
                    self._task_manager.complete(task.task_id)

            asyncio.create_task(_run_background())

            yield LoopEvent(
                type=LoopEventType.system,
                data={
                    "event": "async_launched",
                    "task_id": task.task_id,
                    "agent_id": child_ctx.agent_id,
                    "agent_name": definition.name,
                    "description": f"Background task launched: {definition.name}",
                },
                turn=0,
            )
            return

        # Foreground: yield all events from the loop
        loop = AgentLoop()
        async for event in loop.run(
            messages=messages,
            tools=tools,
            provider=self._provider,
            system_prompt=system_prompt,
            abort_signal=child_ctx.abort_signal,
            query_source=child_ctx.query_source,
        ):
            yield event

    def _resolve_tools(self, definition: AgentDefinition) -> list[BaseTool]:
        """Resolve the tool list from the definition.

        If the definition specifies tool names, filter the available tools.
        If ``tools`` is ``None``, return all available tools.
        """
        if definition.tools is None:
            return list(self._available_tools)

        tool_map = {t.name: t for t in self._available_tools}
        return [
            tool_map[name]
            for name in definition.tools
            if name in tool_map
        ]
