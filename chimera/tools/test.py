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
            output = result.stdout
            if result.stderr:
                output += f"\n{result.stderr}"
            if not result.success:
                return ToolResult(
                    output=output,
                    error=f"Tests failed (exit code {result.exit_code})",
                )
            return ToolResult(output=output)

        # Run full test suite via env.run_tests()
        test_result = env.run_tests()
        metadata: dict[str, Any] = {}
        for attr in ("passed", "failed", "errors", "total"):
            value = getattr(test_result, attr, None)
            if value is not None:
                metadata[attr] = value
        if getattr(test_result, "success", True) is False:
            failed = metadata.get("failed")
            err = (
                f"{failed} test(s) failed"
                if failed
                else "Test run reported failure"
            )
            return ToolResult(
                output=test_result.output, error=err, metadata=metadata
            )
        return ToolResult(output=test_result.output, metadata=metadata)
