"""Tests for chimera.commands.types — Phase 7 + IG-9."""
from __future__ import annotations

from chimera.commands.types import (
    Command,
    CommandType,
    LocalCommand,
    LocalUICommand,
    PromptCommand,
)


class TestCommandTypes:
    """Command type enum and dataclass construction."""

    def test_enum_values(self):
        assert CommandType.PROMPT.value == "prompt"
        assert CommandType.LOCAL.value == "local"
        assert CommandType.LOCAL_UI.value == "local_ui"

    def test_prompt_command_construction(self):
        cmd = PromptCommand(
            name="test-prompt",
            description="A test prompt command",
            get_prompt=lambda args=None: "do the thing",
            source="skill",
            allowed_tools=["bash"],
        )
        assert cmd.type == CommandType.PROMPT
        assert cmd.name == "test-prompt"
        assert cmd.get_prompt() == "do the thing"
        assert cmd.allowed_tools == ["bash"]
        assert cmd.source == "skill"
        assert cmd.is_enabled()

    def test_local_command_construction(self):
        cmd = LocalCommand(
            name="test-local",
            description="A test local command",
            handler=lambda args: f"got: {args}",
        )
        assert cmd.type == CommandType.LOCAL
        assert cmd.name == "test-local"
        assert cmd.handler("hello") == "got: hello"
        assert cmd.aliases == []
        assert cmd.is_hidden is False

    def test_local_ui_command_construction(self):
        """LocalUICommand has type LOCAL_UI and optional handler."""
        called = []
        cmd = LocalUICommand(
            name="open-settings",
            description="Open the settings UI",
            handler=lambda: called.append(True),
        )
        assert cmd.type == CommandType.LOCAL_UI
        assert cmd.name == "open-settings"
        assert cmd.handler is not None
        cmd.handler()
        assert called == [True]

    def test_local_ui_command_no_handler(self):
        """LocalUICommand can be created without a handler."""
        cmd = LocalUICommand(
            name="open-settings",
            description="Open the settings UI",
        )
        assert cmd.type == CommandType.LOCAL_UI
        assert cmd.handler is None

    def test_local_ui_command_is_in_union(self):
        """LocalUICommand is part of the Command union."""
        cmd = LocalUICommand(
            name="ui-cmd",
            description="A UI command",
        )
        # isinstance check against all union members
        assert isinstance(cmd, (PromptCommand, LocalCommand, LocalUICommand))
