"""Tests for chimera.core.tool_pool — Phase 5."""
from __future__ import annotations

from chimera.core.tool import BaseTool
from chimera.core.tool_pool import DeferredToolConfig, ToolPool
from chimera.types import ToolResult


class _DummyTool(BaseTool):
    """Minimal tool for testing."""

    def __init__(self, name: str):
        self.name = name
        self.description = f"Tool {name}"
        self.parameters = {"type": "object", "properties": {}}

    def execute(self, args, env):
        return ToolResult(output="ok")


class TestToolPool:
    """ToolPool eager/deferred behaviour."""

    def test_all_eager_under_limit(self):
        tools = [_DummyTool(f"tool_{i}") for i in range(5)]
        config = DeferredToolConfig(max_eager_tools=30)
        pool = ToolPool(tools, config=config)
        eager = pool.get_eager_tools()
        assert len(eager) == 5
        assert all(isinstance(t, BaseTool) for t in eager)

    def test_defers_over_limit(self):
        tools = [_DummyTool(f"tool_{i}") for i in range(40)]
        config = DeferredToolConfig(max_eager_tools=10, always_eager={"tool_0", "tool_1"})
        pool = ToolPool(tools, config=config)
        eager = pool.get_eager_tools()
        # Should contain always_eager tools + ToolSearchTool placeholder
        eager_names = {t.name for t in eager}
        assert "tool_0" in eager_names
        assert "tool_1" in eager_names
        # Should NOT contain all 40 tools
        assert len(eager) < 40
        # get_all_tools still returns everything
        assert len(pool.get_all_tools()) == 40
