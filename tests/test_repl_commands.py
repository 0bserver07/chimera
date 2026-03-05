"""Tests for REPL slash commands and readline integration."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chimera.cli.code import (
    _COMMANDS,
    _dispatch_command,
    _complete_command,
    cmd_audit,
    cmd_checkpoint,
    cmd_agent,
)


class TestCommandDispatch:
    def test_known_commands_registered(self) -> None:
        """All expected commands exist in the registry."""
        assert "help" in _COMMANDS
        assert "model" in _COMMANDS
        assert "cost" in _COMMANDS
        assert "clear" in _COMMANDS
        assert "tools" in _COMMANDS
        assert "context" in _COMMANDS
        assert "debug" in _COMMANDS
        assert "exit" in _COMMANDS
        assert "quit" in _COMMANDS

    def test_help_command(self) -> None:
        """help command lists all available commands."""
        output: list[str] = []
        session = MagicMock()
        env = MagicMock()
        _COMMANDS["help"](session, env, "", output.append)
        text = "\n".join(output)
        assert "/help" in text
        assert "/model" in text
        assert "/cost" in text

    def test_unknown_command(self) -> None:
        """Unknown commands produce error message."""
        output: list[str] = []
        result = _dispatch_command("/unknown", MagicMock(), MagicMock(), output.append)
        assert result is False
        assert any("unknown" in line.lower() for line in output)

    def test_dispatch_returns_true_for_known(self) -> None:
        """Dispatch returns True for known commands."""
        output: list[str] = []
        session = MagicMock()
        session.debug = False
        result = _dispatch_command("/debug", session, MagicMock(), output.append)
        assert result is True

    def test_dispatch_non_slash(self) -> None:
        """Non-slash input returns False."""
        result = _dispatch_command("hello", MagicMock(), MagicMock(), lambda x: None)
        assert result is False


class TestTabCompletion:
    def test_complete_slash(self) -> None:
        """Tab completion returns matching commands."""
        matches = _complete_command("/he", 0)
        assert matches == "/help"

    def test_complete_no_match(self) -> None:
        """No matching completion returns None."""
        result = _complete_command("/zzz", 0)
        assert result is None

    def test_complete_state_increments(self) -> None:
        """Higher state returns None when no more matches."""
        result = _complete_command("/he", 1)
        assert result is None  # Only one match for /he


class TestCostCommand:
    def test_cost_with_tracker(self) -> None:
        """cost command shows cumulative cost."""
        from chimera.providers.cost_tracker import CostTracker

        output: list[str] = []
        session = MagicMock()
        session.cost_tracker = CostTracker()
        session.cost_tracker.record(0.05, model="gpt-4o")
        session.cost_tracker.record(0.10, model="claude-sonnet-4")
        env = MagicMock()

        _COMMANDS["cost"](session, env, "", output.append)
        text = "\n".join(output)
        assert "0.15" in text or "0.1500" in text

    def test_cost_no_tracker(self) -> None:
        """cost command without tracker shows message."""
        output: list[str] = []
        session = MagicMock(spec=[])  # no attributes
        env = MagicMock()
        _COMMANDS["cost"](session, env, "", output.append)
        assert any("no cost" in line.lower() for line in output)


class TestClearCommand:
    def test_clear_resets_context(self) -> None:
        """clear command calls session.clear()."""
        session = MagicMock()
        env = MagicMock()
        output: list[str] = []
        _COMMANDS["clear"](session, env, "", output.append)
        session.clear.assert_called_once()


class TestDebugCommand:
    def test_debug_toggle(self) -> None:
        """debug command toggles debug mode."""
        session = MagicMock()
        session.debug = False
        env = MagicMock()
        output: list[str] = []

        _COMMANDS["debug"](session, env, "", output.append)
        assert session.debug is True

        _COMMANDS["debug"](session, env, "", output.append)
        assert session.debug is False


class TestCmdAudit:
    def test_no_audit_log(self) -> None:
        session = MagicMock(spec=[])
        messages: list[str] = []
        cmd_audit(session, None, "", messages.append)
        assert messages == ["No audit log active."]

    def test_audit_summary(self) -> None:
        session = MagicMock()
        session.audit_log.summary.return_value = {"approved": 3, "denied": 1}
        messages: list[str] = []
        cmd_audit(session, None, "", messages.append)
        assert messages[0] == "Audit summary:"
        assert any("approved: 3" in m for m in messages)
        assert any("denied: 1" in m for m in messages)

    def test_audit_empty_summary(self) -> None:
        session = MagicMock()
        session.audit_log.summary.return_value = {}
        messages: list[str] = []
        cmd_audit(session, None, "", messages.append)
        assert messages == ["Audit log is empty."]

    def test_audit_clear(self) -> None:
        session = MagicMock()
        messages: list[str] = []
        cmd_audit(session, None, "clear", messages.append)
        session.audit_log.clear.assert_called_once()
        assert messages == ["Audit log cleared."]


class TestCmdCheckpoint:
    def test_no_checkpoint_manager(self) -> None:
        session = MagicMock(spec=[])
        messages: list[str] = []
        cmd_checkpoint(session, None, "", messages.append)
        assert messages == ["No checkpoint manager active."]

    def test_checkpoint_save(self) -> None:
        session = MagicMock()
        info = MagicMock()
        info.name = "my-cp"
        info.id = "abc123"
        session.checkpoint_manager.create.return_value = info
        messages: list[str] = []
        cmd_checkpoint(session, None, "save my-cp", messages.append)
        session.checkpoint_manager.create.assert_called_once_with(name="my-cp")
        assert "my-cp" in messages[0]

    def test_checkpoint_list_empty(self) -> None:
        session = MagicMock()
        session.checkpoint_manager.list_checkpoints.return_value = []
        messages: list[str] = []
        cmd_checkpoint(session, None, "list", messages.append)
        assert messages == ["No checkpoints."]

    def test_checkpoint_list(self) -> None:
        session = MagicMock()
        cp = MagicMock()
        cp.id = "abc"
        cp.name = "first"
        cp.time_str = "2026-01-01 12:00:00"
        session.checkpoint_manager.list_checkpoints.return_value = [cp]
        messages: list[str] = []
        cmd_checkpoint(session, None, "list", messages.append)
        assert any("first" in m for m in messages)

    def test_checkpoint_undo(self) -> None:
        session = MagicMock()
        info = MagicMock()
        info.name = "cp-1"
        session.checkpoint_manager.undo.return_value = info
        messages: list[str] = []
        cmd_checkpoint(session, None, "undo", messages.append)
        session.checkpoint_manager.undo.assert_called_once()
        assert "cp-1" in messages[0]

    def test_checkpoint_undo_none(self) -> None:
        session = MagicMock()
        session.checkpoint_manager.undo.return_value = None
        messages: list[str] = []
        cmd_checkpoint(session, None, "undo", messages.append)
        assert messages == ["No checkpoints to undo."]

    def test_checkpoint_restore(self) -> None:
        session = MagicMock()
        info = MagicMock()
        info.name = "saved"
        session.checkpoint_manager.restore_by_name.return_value = info
        messages: list[str] = []
        cmd_checkpoint(session, None, "restore saved", messages.append)
        session.checkpoint_manager.restore_by_name.assert_called_once_with("saved")
        assert "saved" in messages[0]

    def test_checkpoint_restore_no_name(self) -> None:
        session = MagicMock()
        messages: list[str] = []
        cmd_checkpoint(session, None, "restore", messages.append)
        assert "Usage" in messages[0]


class TestCmdAgent:
    def test_agent_list(self) -> None:
        messages: list[str] = []
        cmd_agent(MagicMock(), None, "list", messages.append)
        assert messages[0] == "Available agent presets:"
        preset_names = [m.strip() for m in messages[1:]]
        assert "build" in preset_names
        assert "explore" in preset_names
        assert "general" in preset_names
        assert "plan" in preset_names
        assert "review" in preset_names

    def test_agent_set_placeholder(self) -> None:
        messages: list[str] = []
        cmd_agent(MagicMock(), None, "set build", messages.append)
        assert "not yet supported" in messages[0]

    def test_agent_default_is_list(self) -> None:
        messages: list[str] = []
        cmd_agent(MagicMock(), None, "", messages.append)
        assert messages[0] == "Available agent presets:"


class TestNewCommandsRegistry:
    def test_commands_contains_audit(self) -> None:
        assert "audit" in _COMMANDS

    def test_commands_contains_checkpoint(self) -> None:
        assert "checkpoint" in _COMMANDS

    def test_commands_contains_agent(self) -> None:
        assert "agent" in _COMMANDS
