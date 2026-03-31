"""Tests for concurrency and safety flags on BaseTool (Task 5, Phase 1)."""

from chimera.core.tool import BaseTool
from chimera.types import ToolResult


class _MinimalTool(BaseTool):
    name = "minimal"
    description = "A minimal tool with no overrides."
    parameters: dict = {"type": "object", "properties": {}}

    def execute(self, args, env) -> ToolResult:
        return ToolResult(output="ok")


class _ReadOnlyConcurrentTool(BaseTool):
    name = "readonly_concurrent"
    description = "A read-only, concurrency-safe tool."
    parameters: dict = {"type": "object", "properties": {}}
    is_concurrency_safe: bool = True
    is_read_only: bool = True

    def execute(self, args, env) -> ToolResult:
        return ToolResult(output="ok")


def test_default_flags():
    tool = _MinimalTool()
    assert tool.is_concurrency_safe is False
    assert tool.is_read_only is False
    assert tool.is_destructive is False
    assert tool.max_result_size_chars == 30_000


def test_readonly_tool_is_concurrent():
    tool = _ReadOnlyConcurrentTool()
    assert tool.is_concurrency_safe is True
    assert tool.is_read_only is True
    # Defaults still hold for unset flags
    assert tool.is_destructive is False
    assert tool.max_result_size_chars == 30_000
