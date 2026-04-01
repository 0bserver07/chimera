"""Tests for chimera.core.context_assembler — Phase 5."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from chimera.core.context_assembler import ContextAssembler
from chimera.core.tool import BaseTool
from chimera.types import ToolResult


class _DummyTool(BaseTool):
    """Minimal tool for testing."""

    def __init__(self, name: str):
        self.name = name
        self.description = f"Tool {name}"
        self.parameters = {"type": "object", "properties": {}}

    def execute(self, args, env):
        return ToolResult(output="ok")


class TestContextAssembler:
    """ContextAssembler layer assembly."""

    @pytest.mark.asyncio
    async def test_default_prompt_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assembler = ContextAssembler(
                project_dir=Path(tmpdir),
                tools=[_DummyTool("read")],
                model="claude-3",
            )
            prompt = await assembler.assemble()
            text = prompt.to_string()
            # Should contain default prompt content
            assert len(text) > 0
            # Should contain tool description
            assert "read" in text

    @pytest.mark.asyncio
    async def test_loads_chimera_md(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            chimera_md = Path(tmpdir) / "CHIMERA.md"
            chimera_md.write_text("# Custom project rules\nAlways test first.")
            assembler = ContextAssembler(
                project_dir=Path(tmpdir),
                tools=[],
                model="claude-3",
            )
            prompt = await assembler.assemble()
            text = prompt.to_string()
            assert "Custom project rules" in text
            assert "Always test first." in text

    @pytest.mark.asyncio
    async def test_agent_override_with_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assembler = ContextAssembler(
                project_dir=Path(tmpdir),
                tools=[],
                model="claude-3",
            )
            # Agent definition overrides default prompt
            prompt = await assembler.assemble(
                agent_definition="You are a code reviewer."
            )
            text = prompt.to_string()
            assert "code reviewer" in text
