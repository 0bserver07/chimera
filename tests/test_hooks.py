# tests/test_hooks.py
"""Tests for the PreToolUse path validation hook."""
import json
import os
import subprocess
import sys


from chimera.hooks.validate_path import validate, _find_suggestions


class TestValidatePathHook:
    """Tests for chimera.hooks.validate_path.validate()."""

    def test_allows_existing_file(self, tmp_path):
        """Hook allows tool calls targeting existing files."""
        target = tmp_path / "main.py"
        target.write_text("print('hello')")

        tool_input = {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(target)},
        }

        allowed, message = validate(tool_input)
        assert allowed is True
        assert message == ""

    def test_blocks_nonexistent_file_with_suggestions(self, tmp_path):
        """Hook blocks edits to nonexistent files and suggests similar ones."""
        # Create some real files
        (tmp_path / "controller.py").write_text("class Controller: pass")
        (tmp_path / "service.py").write_text("class Service: pass")

        # Try to edit a file that doesn't exist
        tool_input = {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(tmp_path / "controler.py")},  # typo
        }

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            allowed, message = validate(tool_input)
        finally:
            os.chdir(old_cwd)

        assert allowed is False
        assert "File not found" in message
        assert "controler.py" in message

    def test_handles_missing_input(self):
        """Hook passes through gracefully when no input is provided."""
        allowed, message = validate({})
        assert allowed is True
        assert message == ""

    def test_passes_non_checked_tools(self):
        """Hook allows tool calls for tools that aren't Write/Edit."""
        tool_input = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/nonexistent/path.py"},
        }

        allowed, message = validate(tool_input)
        assert allowed is True

    def test_exit_codes_via_subprocess(self, tmp_path):
        """Hook script returns correct exit codes when run as subprocess."""
        target = tmp_path / "exists.py"
        target.write_text("x = 1")

        hook_script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "chimera", "hooks", "validate_path.py",
        )

        # Existing file → exit 0
        tool_input = {
            "tool_name": "Write",
            "tool_input": {"file_path": str(target)},
        }
        result = subprocess.run(
            [sys.executable, hook_script],
            input=json.dumps(tool_input),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

        # Nonexistent file → exit 2
        tool_input_bad = {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(tmp_path / "nope.py")},
        }
        result_bad = subprocess.run(
            [sys.executable, hook_script],
            input=json.dumps(tool_input_bad),
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert result_bad.returncode == 2
        assert "File not found" in result_bad.stderr


class TestFindSuggestions:
    """Tests for the fuzzy file suggestion logic."""

    def test_finds_exact_filename_match(self, tmp_path):
        """Suggests files with identical names in different directories."""
        subdir = tmp_path / "src" / "models"
        subdir.mkdir(parents=True)
        (subdir / "user.py").write_text("class User: pass")

        suggestions = _find_suggestions("user.py", str(tmp_path))
        assert any("user.py" in s for s in suggestions)

    def test_returns_empty_for_no_matches(self, tmp_path):
        """Returns empty list when no similar files exist."""
        suggestions = _find_suggestions("zzz_unique_name_12345.py", str(tmp_path))
        assert suggestions == []
