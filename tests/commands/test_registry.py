"""Tests for chimera.commands.registry — Phase 7."""
from __future__ import annotations

from chimera.commands.registry import CommandRegistry
from chimera.commands.types import LocalCommand, PromptCommand


class TestCommandRegistry:
    """CommandRegistry stores, finds, lists, and filters commands."""

    def test_register_and_find(self):
        reg = CommandRegistry()
        cmd = LocalCommand(name="help", description="Show help", handler=lambda args: "help text")
        reg.register(cmd)
        assert reg.find("help") is cmd

    def test_find_by_alias(self):
        reg = CommandRegistry()
        cmd = LocalCommand(
            name="help",
            description="Show help",
            aliases=["h", "?"],
            handler=lambda args: "help text",
        )
        reg.register(cmd)
        assert reg.find("h") is cmd
        assert reg.find("?") is cmd
        assert reg.find("nonexistent") is None

    def test_list_excludes_hidden(self):
        reg = CommandRegistry()
        reg.register(LocalCommand(name="visible", description="v", handler=lambda a: ""))
        reg.register(LocalCommand(name="secret", description="s", handler=lambda a: "", is_hidden=True))
        visible = reg.list_commands(include_hidden=False)
        assert len(visible) == 1
        assert visible[0].name == "visible"
        all_cmds = reg.list_commands(include_hidden=True)
        assert len(all_cmds) == 2

    def test_list_excludes_disabled(self):
        reg = CommandRegistry()
        reg.register(LocalCommand(name="on", description="on", handler=lambda a: "", is_enabled=lambda: True))
        reg.register(LocalCommand(name="off", description="off", handler=lambda a: "", is_enabled=lambda: False))
        listed = reg.list_commands()
        assert len(listed) == 1
        assert listed[0].name == "on"

    def test_model_invocable_filters_builtin(self):
        reg = CommandRegistry()
        reg.register(PromptCommand(
            name="builtin-cmd",
            description="builtin",
            source="builtin",
            get_prompt=lambda: "hi",
        ))
        reg.register(PromptCommand(
            name="skill-cmd",
            description="skill",
            source="skill",
            get_prompt=lambda: "hi",
        ))
        reg.register(PromptCommand(
            name="disabled-cmd",
            description="disabled",
            source="skill",
            disable_model_invocation=True,
            get_prompt=lambda: "hi",
        ))
        invocable = reg.get_model_invocable()
        names = [c.name for c in invocable]
        assert "skill-cmd" in names
        assert "builtin-cmd" not in names
        assert "disabled-cmd" not in names
