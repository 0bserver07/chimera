"""Tests for file tools populating FileChange metadata."""
from __future__ import annotations

from unittest.mock import MagicMock


from chimera.env.base import Environment
from chimera.tools.edit import EditFileTool
from chimera.tools.replace_in_file import ReplaceInFileTool
from chimera.tools.write import WriteFileTool
from chimera.types import ChangeType, CommandResult, FileChange, TestResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeEnv(Environment):
    """In-memory environment for testing file tools."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self._files: dict[str, str] = dict(files or {})

    def setup(self) -> None:
        pass

    def cleanup(self) -> None:
        pass

    def read_file(self, path: str) -> str:
        if path not in self._files:
            raise FileNotFoundError(path)
        return self._files[path]

    def write_file(self, path: str, content: str) -> None:
        self._files[path] = content

    def list_files(self, pattern: str = "**/*") -> list[str]:
        return list(self._files.keys())

    def run_command(self, cmd: str, timeout: int = 120, shell_name: str = "main") -> CommandResult:
        return CommandResult(stdout="", stderr="", exit_code=0)

    def run_tests(self) -> TestResult:
        return TestResult(passed=0, failed=0, errors=0, output="")

    def checkpoint(self) -> str:
        return "fake"

    def restore(self, checkpoint_id: str) -> None:
        pass


# ---------------------------------------------------------------------------
# Tests: WriteFileTool
# ---------------------------------------------------------------------------

class TestWriteFileTool:
    def test_new_file_create_change(self) -> None:
        env = FakeEnv()
        tool = WriteFileTool()
        result = tool.execute({"path": "new.py", "content": "hello"}, env)

        assert result.success
        assert "new.py" in result.output
        assert "file_change" in result.metadata
        fc = result.metadata["file_change"]
        assert isinstance(fc, FileChange)
        assert fc.change_type == ChangeType.CREATE
        assert fc.before_content is None
        assert fc.after_content == "hello"
        assert fc.diff is not None

    def test_existing_file_edit_change(self) -> None:
        env = FakeEnv({"existing.py": "old content"})
        tool = WriteFileTool()
        result = tool.execute({"path": "existing.py", "content": "new content"}, env)

        assert result.success
        fc = result.metadata["file_change"]
        assert fc.change_type == ChangeType.EDIT
        assert fc.before_content == "old content"
        assert fc.after_content == "new content"

    def test_error_no_file_change(self) -> None:
        env = MagicMock(spec=Environment)
        env.read_file.side_effect = FileNotFoundError
        env.write_file.side_effect = PermissionError("nope")
        tool = WriteFileTool()
        result = tool.execute({"path": "bad.py", "content": "x"}, env)

        assert not result.success
        assert "file_change" not in result.metadata


# ---------------------------------------------------------------------------
# Tests: EditFileTool
# ---------------------------------------------------------------------------

class TestEditFileTool:
    def test_successful_edit(self) -> None:
        env = FakeEnv({"foo.py": "hello world"})
        tool = EditFileTool()
        result = tool.execute({
            "path": "foo.py",
            "old_string": "hello",
            "new_string": "goodbye",
        }, env)

        assert result.success
        fc = result.metadata["file_change"]
        assert isinstance(fc, FileChange)
        assert fc.change_type == ChangeType.EDIT
        assert fc.before_content == "hello world"
        assert fc.after_content == "goodbye world"
        assert "hello" in fc.diff
        assert "goodbye" in fc.diff

    def test_file_not_found_no_change(self) -> None:
        env = FakeEnv()
        tool = EditFileTool()
        result = tool.execute({
            "path": "missing.py",
            "old_string": "x",
            "new_string": "y",
        }, env)

        assert not result.success
        assert "file_change" not in result.metadata

    def test_string_not_found_no_change(self) -> None:
        env = FakeEnv({"foo.py": "hello"})
        tool = EditFileTool()
        result = tool.execute({
            "path": "foo.py",
            "old_string": "nonexistent",
            "new_string": "y",
        }, env)

        assert not result.success
        assert "file_change" not in result.metadata


# ---------------------------------------------------------------------------
# Tests: ReplaceInFileTool
# ---------------------------------------------------------------------------

class TestReplaceInFileTool:
    def test_successful_replace(self) -> None:
        env = FakeEnv({"code.py": "foo = 1\nbar = 2\nfoo = 3"})
        tool = ReplaceInFileTool()
        result = tool.execute({
            "path": "code.py",
            "pattern": r"foo",
            "replacement": "baz",
        }, env)

        assert result.success
        fc = result.metadata["file_change"]
        assert isinstance(fc, FileChange)
        assert fc.change_type == ChangeType.EDIT
        assert "foo" in fc.before_content
        assert "baz" in fc.after_content

    def test_zero_replacements_no_change(self) -> None:
        env = FakeEnv({"code.py": "hello"})
        tool = ReplaceInFileTool()
        result = tool.execute({
            "path": "code.py",
            "pattern": r"nonexistent",
            "replacement": "x",
        }, env)

        assert result.success  # 0 replacements is not an error
        assert "file_change" not in result.metadata

    def test_file_not_found(self) -> None:
        env = FakeEnv()
        tool = ReplaceInFileTool()
        result = tool.execute({
            "path": "missing.py",
            "pattern": "x",
            "replacement": "y",
        }, env)

        assert not result.success
        assert "file_change" not in result.metadata
