"""Tests for chimera.tools.skill_tool — Phase 7 + IG-8."""
from __future__ import annotations

from chimera.commands.registry import CommandRegistry
from chimera.commands.types import PromptCommand
from chimera.tools.skill_tool import SkillTool


class TestSkillTool:
    """SkillTool invokes skills through the command registry."""

    def test_invokes_inline_skill(self):
        reg = CommandRegistry()
        reg.register(PromptCommand(
            name="review",
            description="Review code",
            source="skill",
            allowed_tools=["bash", "read"],
            get_prompt=lambda args=None: f"Review this: {args}" if args else "Review this",
        ))

        tool = SkillTool(reg)
        result = tool.execute({"skill": "review", "args": "main.py"}, env=None)
        assert result.success
        assert "main.py" in result.output
        assert result.metadata["inline_prompt"] == result.output
        assert "bash" in result.metadata["allowed_tools"]

    def test_unknown_skill_returns_error(self):
        reg = CommandRegistry()
        tool = SkillTool(reg)
        result = tool.execute({"skill": "nonexistent"}, env=None)
        assert result.error is not None
        assert "nonexistent" in result.error

    def test_fork_context_sync_returns_error_when_spawner_set(self):
        """When a fork-context command is invoked sync with a spawner, it errors."""
        reg = CommandRegistry()
        reg.register(PromptCommand(
            name="deep-review",
            description="Deep review",
            source="skill",
            context="fork",
            get_prompt=lambda args=None: "Review deeply",
        ))

        # Use a sentinel object as the spawner
        tool = SkillTool(reg, spawner=object())
        result = tool.execute({"skill": "deep-review"}, env=None)
        assert result.error is not None
        assert "fork" in result.error.lower() or "async" in result.error.lower()

    def test_fork_context_without_spawner_falls_through_to_inline(self):
        """Without a spawner, fork commands fall through to inline behavior."""
        reg = CommandRegistry()
        reg.register(PromptCommand(
            name="deep-review",
            description="Deep review",
            source="skill",
            context="fork",
            get_prompt=lambda args=None: "Review deeply",
        ))

        tool = SkillTool(reg, spawner=None)
        result = tool.execute({"skill": "deep-review"}, env=None)
        assert result.success
        assert result.output == "Review deeply"
