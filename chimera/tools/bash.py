from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class BashTool(BaseTool):
    name = "bash"
    description = "Execute a shell command and return its output."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 120},
        },
        "required": ["command"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        timeout = args.get("timeout", 120)
        result = env.run_command(args["command"], timeout=timeout)
        output = result.stdout
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"
        if result.success:
            return ToolResult(output=output)
        else:
            return ToolResult(output=output, error=f"Exit code {result.exit_code}")
