"""Tests for chimera.commands.builtins — Phase 7 + new commands."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

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


class TestExpandedBuiltinCommands:
    """Tests for the expanded set of 25+ built-in commands."""

    def _find(self, name: str) -> LocalCommand:
        commands = get_builtin_commands()
        return next(c for c in commands if c.name == name)

    # --- Count ---

    def test_builtin_count_at_least_25(self):
        commands = get_builtin_commands()
        assert len(commands) >= 25, f"Expected >= 25 commands, got {len(commands)}"

    # --- Help lists all commands ---

    def test_help_lists_all_commands(self):
        commands = get_builtin_commands()
        help_cmd = self._find("help")
        result = help_cmd.handler("")
        for cmd in commands:
            if not cmd.is_hidden:
                assert cmd.name in result, f"/{cmd.name} not found in /help output"

    # --- Session management ---

    def test_session_command_exists(self):
        cmd = self._find("session")
        assert cmd.description
        assert "s" in cmd.aliases

    def test_session_handler_info(self):
        cmd = self._find("session")
        result = cmd.handler("")
        assert isinstance(result, str)
        assert "session" in result.lower()

    def test_session_handler_unknown_subcommand(self):
        cmd = self._find("session")
        result = cmd.handler("foobar")
        assert "foobar" in result

    def test_files_command_exists(self):
        cmd = self._find("files")
        assert cmd.description
        result = cmd.handler("")
        assert isinstance(result, str)

    def test_history_command_exists(self):
        cmd = self._find("history")
        assert cmd.description
        result = cmd.handler("")
        assert isinstance(result, str)

    # --- Development ---

    def test_commit_handler_runs(self):
        cmd = self._find("commit")
        assert cmd.description
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="[main abc1234] Auto-commit by chimera\n 1 file changed",
                stderr="",
            )
            result = cmd.handler("")
            assert isinstance(result, str)
            assert mock_run.call_count == 2  # git add + git commit

    def test_commit_handler_custom_message(self):
        cmd = self._find("commit")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="[main abc1234] my message\n",
                stderr="",
            )
            result = cmd.handler("my message")
            # Second call is git commit, check -m arg
            commit_call = mock_run.call_args_list[1]
            assert "my message" in commit_call[0][0]

    def test_commit_handler_error(self):
        cmd = self._find("commit")
        with patch("subprocess.run", side_effect=OSError("git not found")):
            result = cmd.handler("")
            assert "Error" in result

    def test_test_command_exists(self):
        cmd = self._find("test")
        assert cmd.description

    def test_test_handler_runs(self):
        cmd = self._find("test")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="5 passed\n",
                stderr="",
            )
            result = cmd.handler("")
            assert "passed" in result

    def test_test_handler_custom_command(self):
        cmd = self._find("test")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="ok\n",
                stderr="",
            )
            result = cmd.handler("python -m unittest")
            called_cmd = mock_run.call_args[0][0]
            assert "unittest" in called_cmd

    # --- Context ---

    def test_context_command_exists(self):
        cmd = self._find("context")
        assert cmd.description
        result = cmd.handler("")
        assert isinstance(result, str)

    def test_debug_command_exists(self):
        cmd = self._find("debug")
        assert cmd.description
        result = cmd.handler("")
        assert isinstance(result, str)

    def test_verbose_command_exists(self):
        cmd = self._find("verbose")
        assert cmd.description
        result = cmd.handler("")
        assert isinstance(result, str)

    # --- Configuration ---

    def test_permissions_command_exists(self):
        cmd = self._find("permissions")
        assert cmd.description
        assert "perms" in cmd.aliases

    def test_hooks_command_exists(self):
        cmd = self._find("hooks")
        assert cmd.description
        result = cmd.handler("")
        assert isinstance(result, str)

    def test_preset_handler_lists_presets(self):
        cmd = self._find("preset")
        result = cmd.handler("")
        assert isinstance(result, str)
        assert "claude_code" in result
        assert "codex" in result
        assert "minimal" in result
        assert "explore" in result

    # --- Information ---

    def test_version_handler(self):
        cmd = self._find("version")
        result = cmd.handler("")
        assert isinstance(result, str)
        assert "chimera" in result.lower()

    def test_snapshot_command_exists(self):
        cmd = self._find("snapshot")
        assert cmd.description
        assert "snap" in cmd.aliases
        result = cmd.handler("")
        assert isinstance(result, str)

    def test_tokens_command_exists(self):
        cmd = self._find("tokens")
        assert cmd.description
        result = cmd.handler("")
        assert isinstance(result, str)

    def test_env_handler_shows_cwd(self):
        cmd = self._find("env")
        result = cmd.handler("")
        assert isinstance(result, str)
        assert "CWD:" in result

    def test_env_handler_shows_model_vars(self):
        cmd = self._find("env")
        result = cmd.handler("")
        assert "ANTHROPIC_MODEL:" in result
        assert "OPENAI_MODEL:" in result

    # --- Alias tests ---

    def test_status_handler_alias_for_diff(self):
        """The /status command has alias 'st'."""
        cmd = self._find("status")
        assert "st" in cmd.aliases
        result = cmd.handler("")
        assert isinstance(result, str)

    # --- All commands have handlers ---

    def test_all_commands_have_callable_handlers(self):
        commands = get_builtin_commands()
        for cmd in commands:
            assert callable(cmd.handler), f"/{cmd.name} handler is not callable"

    def test_all_commands_have_descriptions(self):
        commands = get_builtin_commands()
        for cmd in commands:
            assert cmd.description, f"/{cmd.name} has no description"

    # --- Help categories ---

    def test_help_shows_categories(self):
        cmd = self._find("help")
        result = cmd.handler("")
        # Should have category headers
        assert "Session" in result or "session" in result
        assert "Development" in result or "development" in result
        assert "Information" in result or "information" in result
