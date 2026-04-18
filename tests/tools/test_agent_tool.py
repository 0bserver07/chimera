"""Tests for chimera.tools.agent_tool — IG-5."""
from __future__ import annotations

import pytest

from chimera.tools.agent_tool import AgentTool


class TestAgentTool:
    """AgentTool launches sub-agents via a spawner."""

    def test_sync_execute_returns_error(self):
        """Sync execute should tell caller to use async_execute."""
        tool = AgentTool()
        result = tool.execute(
            {"description": "test", "prompt": "do stuff"}, env=None,
        )
        assert result.error is not None
        assert "async" in result.error.lower()

    @pytest.mark.asyncio
    async def test_async_execute_no_spawner(self):
        """When no spawner is configured, async_execute returns an error."""
        tool = AgentTool()
        result = await tool.async_execute(
            {"description": "test task", "prompt": "do the thing"}, env=None,
        )
        assert "spawner" in result.error.lower()

    def test_tool_metadata(self):
        """AgentTool has correct name, description, and parameters."""
        tool = AgentTool()
        assert tool.name == "agent"
        assert "sub-agent" in tool.description.lower() or "agent" in tool.description.lower()
        assert "prompt" in tool.parameters["properties"]
        assert "description" in tool.parameters["properties"]
        assert tool.is_concurrency_safe is False
