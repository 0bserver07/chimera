"""Tests for chimera.commands.builtins — Phase 7 + new commands."""
from __future__ import annotations

from chimera.commands.builtins import get_builtin_commands
from chimera.commands.types import LocalCommand


class TestBuiltinCommands:
    """Built-in commands are returned as LocalCommand instances."""

    def test_get_builtin_commands_returns_list(self):
        commands = get_builtin_commands()
        assert isinstance(commands, list)
        assert len(commands) >= 5
        assert all(isinstance(c, LocalCommand) for c in commands)
        names = [c.name for c in commands]
        assert "help" in names
        assert "clear" in names
        assert "exit" in names

    def test_help_handler_returns_string(self):
        commands = get_builtin_commands()
        help_cmd = next(c for c in commands if c.name == "help")
        result = help_cmd.handler("")
        assert isinstance(result, str)
        assert "help" in result.lower()


class TestNewBuiltinCommands:
    """Verify /diff, /status, /model, and /memory commands exist and work."""

    def _find(self, name: str) -> LocalCommand:
        commands = get_builtin_commands()
        return next(c for c in commands if c.name == name)

    def test_diff_command_exists(self):
        cmd = self._find("diff")
        assert cmd.description
        result = cmd.handler("")
        assert isinstance(result, str)

    def test_status_command_exists(self):
        cmd = self._find("status")
        assert cmd.description
        result = cmd.handler("")
        assert isinstance(result, str)

    def test_status_has_alias(self):
        cmd = self._find("status")
        assert "st" in cmd.aliases

    def test_model_command_exists(self):
        cmd = self._find("model")
        assert cmd.description
        result = cmd.handler("")
        assert isinstance(result, str)
        assert "model" in result.lower()

    def test_memory_command_exists(self):
        cmd = self._find("memory")
        assert cmd.description
        result = cmd.handler("")
        assert isinstance(result, str)

    def test_memory_has_alias(self):
        cmd = self._find("memory")
        assert "mem" in cmd.aliases

    def test_help_includes_new_commands(self):
        cmd = self._find("help")
        result = cmd.handler("")
        assert "/diff" in result
        assert "/status" in result
        assert "/model" in result
        assert "/memory" in result

    def test_total_command_count(self):
        commands = get_builtin_commands()
        names = [c.name for c in commands]
        assert len(names) >= 9  # original 5 + 4 new
        for expected in ("help", "clear", "compact", "cost", "diff", "status", "model", "memory", "exit"):
            assert expected in names
