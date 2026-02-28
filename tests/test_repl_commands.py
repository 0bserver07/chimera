"""Tests for REPL slash commands and readline integration."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chimera.cli.code import _COMMANDS, _dispatch_command, _complete_command


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
