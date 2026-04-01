"""Tests for chimera.tools.tool_search — Phase 5."""
from __future__ import annotations

from chimera.core.tool import BaseTool
from chimera.tools.tool_search import ToolSearchTool
from chimera.types import ToolResult


class _DummyTool(BaseTool):
    """Minimal tool for testing."""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.parameters = {"type": "object", "properties": {}}

    def execute(self, args, env):
        return ToolResult(output="ok")


class TestToolSearchTool:
    """ToolSearchTool should find matching tools by name and description."""

    def test_search_finds_matching_tools(self):
        tools = [
            _DummyTool("read_file", "Read a file from disk"),
            _DummyTool("write_file", "Write content to a file"),
            _DummyTool("bash", "Execute shell commands"),
            _DummyTool("search", "Search codebase for patterns"),
        ]
        search_tool = ToolSearchTool(tools)
        result = search_tool.execute({"query": "file"}, env=None)
        assert not result.error
        # Should match read_file and write_file (by name) — both have "file"
        assert "read_file" in result.output
        assert "write_file" in result.output
        # bash doesn't match "file"
        assert "bash" not in result.output
