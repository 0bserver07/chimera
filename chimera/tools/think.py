# chimera/tools/think.py
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class ThinkTool(BaseTool):
    """Scratchpad for agent reasoning.

    No external action is performed — the value is that the thought gets
    logged in context, allowing the agent to reason step by step before
    taking an action.
    """

    name = "think"
    description = (
        "Think through a problem step by step without taking any external "
        "action. Use this to reason about complex decisions before acting."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "thought": {
                "type": "string",
                "description": "Your reasoning or analysis",
            },
        },
        "required": ["thought"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        thought = args["thought"]
        return ToolResult(output="Thought recorded.", metadata={"thought": thought})
