"""Tool that lets the model invoke a skill or slash command by name."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chimera.commands.registry import CommandRegistry
from chimera.commands.types import LocalCommand, PromptCommand
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.core.agent_spawner import AgentSpawner


class SkillTool(BaseTool):
    """Execute a skill or slash command via the tool-use interface."""

    name = "skill"
    description = "Execute a skill or slash command"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "Name of the skill or command to execute",
            },
            "args": {
                "type": "string",
                "description": "Arguments to pass to the skill",
            },
        },
        "required": ["skill"],
    }

    def __init__(
        self,
        registry: CommandRegistry,
        spawner: AgentSpawner | None = None,
    ) -> None:
        self._registry = registry
        self._spawner = spawner

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        skill_name = args.get("skill", "")
        skill_args = args.get("args", "")

        command = self._registry.find(skill_name)
        if command is None:
            return ToolResult(
                output="",
                error=f"Unknown skill or command: {skill_name}",
            )

        if isinstance(command, PromptCommand):
            # Fork context: delegate to a sub-agent if spawner is available
            if command.context == "fork" and self._spawner is not None:
                return ToolResult(
                    output="",
                    error="Fork commands require async execution; use async_execute",
                    metadata={"fork": True, "skill": skill_name},
                )

            prompt = command.get_prompt({"args": skill_args} if skill_args else None)
            return ToolResult(
                output=prompt,
                metadata={
                    "inline_prompt": prompt,
                    "allowed_tools": command.allowed_tools or [],
                },
            )

        if isinstance(command, LocalCommand):
            result = command.handler(skill_args)
            return ToolResult(output=str(result))

        return ToolResult(output="", error=f"Unsupported command type for: {skill_name}")

    async def async_execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        """Async version — handles fork context by spawning a sub-agent."""
        skill_name = args.get("skill", "")
        skill_args = args.get("args", "")

        command = self._registry.find(skill_name)
        if command is None:
            return ToolResult(
                output="",
                error=f"Unknown skill or command: {skill_name}",
            )

        if (
            isinstance(command, PromptCommand)
            and command.context == "fork"
            and self._spawner is not None
        ):
            from chimera.core.abort import AbortSignal
            from chimera.core.agent_context import AgentContext
            from chimera.core.agent_definition import AgentDefinition
            from chimera.core.loop_events import LoopEventType
            from chimera.core.loop_state import QuerySource

            prompt = command.get_prompt({"args": skill_args} if skill_args else None)
            defn = AgentDefinition(
                name=f"fork-{skill_name}",
                description=f"Forked execution of skill: {skill_name}",
                model=command.model,
            )
            parent_ctx = AgentContext(
                messages=[],
                file_state_cache={},
                abort_signal=AbortSignal(),
                denial_tracking={},
                agent_id="parent",
                parent_agent_id=None,
                query_source=QuerySource.FORK,
                depth=0,
                get_app_state=lambda: {},
                set_app_state=lambda u: None,
                set_app_state_for_tasks=lambda u: None,
            )

            events = []
            async for event in self._spawner.spawn(
                defn, prompt, parent_ctx, run_in_background=True,
            ):
                events.append(event)

            result_event = next(
                (e for e in reversed(events) if e.type == LoopEventType.result),
                None,
            )
            if result_event:
                return ToolResult(output=str(result_event.data.reason))
            return ToolResult(
                output="Forked agent launched",
                metadata={"fork": True, "skill": skill_name},
            )

        # Non-fork: delegate to sync execute
        return self.execute(args, env)
