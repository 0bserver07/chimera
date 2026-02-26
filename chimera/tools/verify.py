# chimera/tools/verify.py
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class VerifyTool(BaseTool):
    """Run Python verification code to cross-check a candidate answer.

    The agent writes Python code that prints True or False.
    The tool executes it and reports whether verification passed.
    """

    name = "verify_answer"
    description = (
        "Run Python verification code to cross-check a candidate answer. "
        "The code should print True if the answer is correct, False otherwise."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code that prints True or False to verify an answer",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default: 30)",
                "default": 30,
            },
        },
        "required": ["code"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        timeout = args.get("timeout", 30)
        code = args["code"]

        result = env.run_command(f'python3 -c {_shell_quote(code)}', timeout=timeout)

        output = result.stdout
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"

        if not result.success:
            return ToolResult(output=output, error=f"Exit code {result.exit_code}")

        verified = result.stdout.strip().split("\n")[-1].strip() == "True"
        return ToolResult(
            output=output,
            metadata={"verified": verified},
        )


def _shell_quote(s: str) -> str:
    """Quote a string for safe shell usage."""
    return "'" + s.replace("'", "'\"'\"'") + "'"
