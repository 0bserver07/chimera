"""AgentTool: launch a sub-agent to handle complex tasks autonomously.

Provides :class:`AgentTool`, a tool that delegates work to a sub-agent
via an :class:`~chimera.core.agent_spawner.AgentSpawner`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.core.agent_spawner import AgentSpawner


class AgentTool(BaseTool):
    """Launch a sub-agent to handle complex tasks autonomously."""

    name = "agent"
    description = "Launch a sub-agent to handle complex tasks autonomously"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "3-5 word task description",
            },
            "prompt": {
                "type": "string",
                "description": "Detailed task for the agent",
            },
            "subagent_type": {
                "type": "string",
                "description": "Agent type (e.g., 'explore', 'plan')",
            },
            "model": {
                "type": "string",
                "enum": ["sonnet", "opus", "haiku"],
            },
            "run_in_background": {
                "type": "boolean",
                "default": False,
            },
        },
        "required": ["description", "prompt"],
    }
    is_concurrency_safe = False

    def __init__(self, spawner: AgentSpawner | None = None) -> None:
        self._spawner = spawner

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        """Sync execute is not supported -- returns an error directing to async."""
        return ToolResult(
            output="Agent tool requires async execution",
            error="Use async_execute",
        )

    async def async_execute(
        self, args: dict[str, Any], env: Environment | None,
    ) -> ToolResult:
        """Spawn a sub-agent and collect its result."""
        if self._spawner is None:
            return ToolResult(
                output="No agent spawner configured",
                error="Spawner not set",
            )

        from chimera.core.abort import AbortSignal
        from chimera.core.agent_context import AgentContext
        from chimera.core.agent_definition import AgentDefinition
        from chimera.core.loop_events import LoopEventType
        from chimera.core.loop_state import QuerySource

        defn = AgentDefinition(
            name=args.get("subagent_type", "general-purpose"),
            description=args["description"],
            model=args.get("model"),
        )

        parent_ctx = AgentContext(
            messages=[],
            file_state_cache={},
            abort_signal=AbortSignal(),
            denial_tracking={},
            agent_id="parent",
            parent_agent_id=None,
            query_source=QuerySource.FOREGROUND,
            depth=0,
            get_app_state=lambda: {},
            set_app_state=lambda u: None,
            set_app_state_for_tasks=lambda u: None,
        )

        events = []
        async for event in self._spawner.spawn(
            defn,
            args["prompt"],
            parent_ctx,
            run_in_background=args.get("run_in_background", False),
        ):
            events.append(event)

        result_event = next(
            (e for e in reversed(events) if e.type == LoopEventType.result),
            None,
        )
        if result_event:
            return ToolResult(output=str(result_event.data.reason))
        return ToolResult(output="Agent completed")
