"""Tests for :mod:`chimera.tools.write_guard` — W13-G13.

Covers the static :class:`WriteGuard` invariants, the :class:`WriteGuardTool`
agent-facing surface, and the integration into
:class:`~chimera.tools.write.WriteFileTool` and
:class:`~chimera.tools.edit.EditFileTool`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chimera.tools.edit import EditFileTool
from chimera.tools.write import WriteFileTool
from chimera.tools.write_guard import (
    WriteGuard,
    WriteGuardError,
    WriteGuardTool,
)


@pytest.fixture(autouse=True)
def _reset_guard():
    """Each test starts with the guard disabled."""
    WriteGuard.reset()
    yield
    WriteGuard.reset()


# ---------------------------------------------------------------------------
# WriteGuard core invariants
# ---------------------------------------------------------------------------


class TestInvariants:
    def test_default_is_disabled(self):
        assert WriteGuard.is_enforced() is False

    def test_set_enforced_round_trip(self):
        WriteGuard.set_enforced(True)
        assert WriteGuard.is_enforced() is True
        WriteGuard.set_enforced(False)
        assert WriteGuard.is_enforced() is False

    def test_check_write_passes_for_missing_file(self, tmp_path: Path):
        target = tmp_path / "new.py"
        # Should not raise even without env, since the file does not exist.
        WriteGuard.check_write(str(target))

    def test_check_write_raises_for_existing_file(self, tmp_path: Path):
        target = tmp_path / "exists.py"
        target.write_text("# already here\n")
        with pytest.raises(WriteGuardError) as exc:
            WriteGuard.check_write(str(target))
        msg = str(exc.value)
        assert "edit_file" in msg
        assert str(target) in msg
        assert exc.value.tool == "write_file"

    def test_check_edit_passes_for_existing_file(self, tmp_path: Path):
        target = tmp_path / "live.py"
        target.write_text("x = 1\n")
        WriteGuard.check_edit(str(target))

    def test_check_edit_raises_for_missing_file(self, tmp_path: Path):
        target = tmp_path / "ghost.py"
        with pytest.raises(WriteGuardError) as exc:
            WriteGuard.check_edit(str(target))
        msg = str(exc.value)
        assert "write_file" in msg
        assert exc.value.tool == "edit_file"

    def test_empty_path_is_a_noop(self):
        # Defensive: check_* must not blow up on a blank path.
        WriteGuard.check_write("")
        WriteGuard.check_edit("")


# ---------------------------------------------------------------------------
# WriteGuardTool behaviour
# ---------------------------------------------------------------------------


class TestWriteGuardTool:
    def test_status_reports_disabled_by_default(self):
        result = WriteGuardTool().execute({"action": "status"}, env=None)
        assert result.error is None
        assert "disabled" in result.output

    def test_enable_then_status_reports_enabled(self):
        tool = WriteGuardTool()
        tool.execute({"action": "enable"}, env=None)
        assert WriteGuard.is_enforced()
        result = tool.execute({"action": "status"}, env=None)
        assert "enabled" in result.output

    def test_disable_turns_guard_off(self):
        WriteGuard.set_enforced(True)
        tool = WriteGuardTool()
        tool.execute({"action": "disable"}, env=None)
        assert WriteGuard.is_enforced() is False

    def test_check_write_action_blocks_existing_path(self, tmp_path: Path):
        target = tmp_path / "x.py"
        target.write_text("# here\n")
        result = WriteGuardTool().execute(
            {"action": "check_write", "path": str(target)}, env=None,
        )
        assert result.error is not None
        assert "edit_file" in result.error

    def test_check_edit_action_blocks_missing_path(self, tmp_path: Path):
        target = tmp_path / "missing.py"
        result = WriteGuardTool().execute(
            {"action": "check_edit", "path": str(target)}, env=None,
        )
        assert result.error is not None
        assert "write_file" in result.error

    def test_check_actions_succeed_when_invariant_holds(self, tmp_path: Path):
        existing = tmp_path / "live.py"
        existing.write_text("a = 1\n")
        ok_edit = WriteGuardTool().execute(
            {"action": "check_edit", "path": str(existing)}, env=None,
        )
        assert ok_edit.error is None
        new_path = tmp_path / "to_create.py"
        ok_write = WriteGuardTool().execute(
            {"action": "check_write", "path": str(new_path)}, env=None,
        )
        assert ok_write.error is None

    def test_unknown_action_returns_error(self):
        result = WriteGuardTool().execute(
            {"action": "stomp"}, env=None,
        )
        assert result.error is not None
        assert "unknown action" in result.error

    def test_check_action_requires_path(self):
        result = WriteGuardTool().execute(
            {"action": "check_write"}, env=None,
        )
        assert result.error is not None
        assert "'path' is required" in result.error


# ---------------------------------------------------------------------------
# Integration with WriteFileTool / EditFileTool
# ---------------------------------------------------------------------------


class _MemEnv:
    """Minimal in-memory env stub for the integration tests."""

    def __init__(self, root: Path):
        self.root = root
        self.cwd = str(root)

    def read_file(self, path: str) -> str:
        full = self.root / path if not Path(path).is_absolute() else Path(path)
        if not full.exists():
            raise FileNotFoundError(path)
        return full.read_text()

    def write_file(self, path: str, content: str) -> None:
        full = self.root / path if not Path(path).is_absolute() else Path(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)


class TestIntegration:
    def test_write_file_clobber_blocked_when_guard_enabled(
        self, tmp_path: Path,
    ):
        env = _MemEnv(tmp_path)
        target = tmp_path / "live.py"
        target.write_text("original\n")

        WriteGuard.set_enforced(True)
        result = WriteFileTool().execute(
            {"path": "live.py", "content": "stomp"}, env=env,
        )
        assert result.error is not None
        assert "edit_file" in result.error
        # File untouched.
        assert target.read_text() == "original\n"

    def test_write_file_passes_when_guard_disabled(self, tmp_path: Path):
        env = _MemEnv(tmp_path)
        target = tmp_path / "live.py"
        target.write_text("original\n")

        # Default state — guard off.
        result = WriteFileTool().execute(
            {"path": "live.py", "content": "stomp"}, env=env,
        )
        assert result.error is None
        assert target.read_text() == "stomp"

    def test_edit_file_missing_blocked_when_guard_enabled(
        self, tmp_path: Path,
    ):
        env = _MemEnv(tmp_path)

        WriteGuard.set_enforced(True)
        result = EditFileTool().execute(
            {
                "path": "ghost.py",
                "old_string": "a",
                "new_string": "b",
            },
            env=env,
        )
        assert result.error is not None
        assert "write_file" in result.error

    def test_edit_file_succeeds_when_guard_blocks_nothing(
        self, tmp_path: Path,
    ):
        env = _MemEnv(tmp_path)
        target = tmp_path / "live.py"
        target.write_text("hello world\n")

        WriteGuard.set_enforced(True)
        result = EditFileTool().execute(
            {
                "path": "live.py",
                "old_string": "world",
                "new_string": "chimera",
            },
            env=env,
        )
        assert result.error is None
        assert "chimera" in target.read_text()


# ---------------------------------------------------------------------------
# Trademark hygiene
# ---------------------------------------------------------------------------


class TestTrademarkHygiene:
    @pytest.mark.parametrize("forbidden", ["codex", "openai"])
    def test_no_brand_strings_in_implementation(self, forbidden: str):
        from chimera.tools import write_guard as module
        source = Path(module.__file__).read_text().lower()
        assert forbidden not in source
