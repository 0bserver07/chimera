# chimera/tools/delegate.py
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class DelegateTool(BaseTool):
    """Wraps an Agent as a tool, enabling sub-agent delegation."""

    description = "Delegate a task to a sub-agent."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The task to delegate to the sub-agent"},
        },
        "required": ["task"],
    }

    def __init__(self, sub_agent: Any, tool_name: str = "delegate") -> None:
        from chimera.core.agent import Agent
        self._sub_agent: Agent = sub_agent
        self.name = tool_name

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        task = args["task"]
        result = self._sub_agent.run(task, env)
        if result.success:
            return ToolResult(output=result.output)
        return ToolResult(output=result.output, error=result.error)
