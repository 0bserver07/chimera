"""Tests for chimera.tools.rollback — Issue #125."""
from __future__ import annotations

from pathlib import Path

import pytest

from chimera.core.snapshot import SnapshotManager
from chimera.tools.rollback import RollbackTool


class TestRollbackTool:
    """RollbackTool reverts files to an earlier checkpoint turn."""

    def test_rollback_no_snapshot_manager(self):
        """Without a snapshot manager, execute returns an error."""
        tool = RollbackTool(snapshot_manager=None)
        result = tool.execute({"checkpoint": 1}, env=None)
        assert result.error is not None
        assert "No snapshot manager" in result.error

    def test_rollback_invalid_turn(self, tmp_path: Path):
        """Requesting a turn that has no snapshot returns an error with available turns."""
        mgr = SnapshotManager(tmp_path)
        tool = RollbackTool(snapshot_manager=mgr)
        result = tool.execute({"checkpoint": 99}, env=None)
        assert result.error is not None
        assert "No snapshot at turn 99" in result.error

    @pytest.mark.asyncio
    async def test_rollback_reverts_files(self, tmp_path: Path):
        """async_execute actually restores files via SnapshotManager."""
        f = tmp_path / "code.py"
        f.write_text("v1")

        mgr = SnapshotManager(tmp_path)
        await mgr.take(turn=1, modified_files=["code.py"])

        # Simulate agent modifying the file
        f.write_text("v2 — broken")
        await mgr.take(turn=2, modified_files=["code.py"])

        tool = RollbackTool(snapshot_manager=mgr)
        result = await tool.async_execute({"checkpoint": 1}, env=None)

        assert result.error is None
        assert "Rolled back to turn 1" in result.output
        assert "code.py" in result.output
        assert f.read_text() == "v1"

    @pytest.mark.asyncio
    async def test_rollback_metadata_includes_turn(self, tmp_path: Path):
        """Sync execute populates metadata with rollback_turn and files_to_revert."""
        f = tmp_path / "data.py"
        f.write_text("original")

        mgr = SnapshotManager(tmp_path)
        await mgr.take(turn=1, modified_files=["data.py"])

        tool = RollbackTool(snapshot_manager=mgr)
        result = tool.execute({"checkpoint": 1, "message": "fix it"}, env=None)

        assert result.error is None
        assert result.metadata["rollback_turn"] == 1
        assert result.metadata["rollback_message"] == "fix it"
        assert "data.py" in result.metadata["files_to_revert"]
