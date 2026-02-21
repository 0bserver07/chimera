# chimera/tools/test.py
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class TestTool(BaseTool):
    name = "test"
    description = "Run the test suite. Optionally specify a path to run specific tests."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Specific test file or directory to run"},
        },
        "required": [],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        path = args.get("path")

        if path:
            # Run specific test file using bash
            result = env.run_command(f"python -m pytest {path} -v")
        else:
            # Run full test suite via env.run_tests()
            test_result = env.run_tests()
            return ToolResult(output=test_result.output)

        output = result.stdout
        if result.stderr:
            output += f"\n{result.stderr}"
        return ToolResult(output=output)
