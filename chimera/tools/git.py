# chimera/tools/git.py
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class GitTool(BaseTool):
    name = "git"
    description = "Run git commands in the workspace. Destructive commands (push --force, reset --hard) are blocked."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Git subcommand and arguments (e.g. 'status', 'add .', 'commit -m msg')"},
        },
        "required": ["command"],
    }

    BLOCKED_PATTERNS = [
        "push --force", "push -f",
        "reset --hard",
        "clean -f", "clean -fd",
        "branch -D",
    ]

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        command = args["command"]

        # Safety check
        for pattern in self.BLOCKED_PATTERNS:
            if pattern in command:
                return ToolResult(output="", error=f"Blocked: 'git {pattern}' is not allowed for safety.")

        result = env.run_command(f"git {command}")
        output = result.stdout
        if result.stderr:
            output += f"\n{result.stderr}"
        if result.success:
            return ToolResult(output=output)
        return ToolResult(output=output, error=f"git {command} failed (exit {result.exit_code})")
