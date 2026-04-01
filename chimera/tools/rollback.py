"""Rollback tool: revert files and conversation to an earlier checkpoint turn.

Issue #125.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.core.snapshot import SnapshotManager


class RollbackTool(BaseTool):
    """Revert files and conversation to an earlier checkpoint turn."""

    name = "rollback"
    description = "Revert files and conversation to an earlier checkpoint turn"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "checkpoint": {"type": "integer", "description": "Turn number to rollback to"},
            "message": {"type": "string", "description": "Correction message for the retry"},
        },
        "required": ["checkpoint"],
    }
    is_concurrency_safe = False
    is_destructive = True

    def __init__(self, snapshot_manager: SnapshotManager | None = None) -> None:
        self._snapshot = snapshot_manager

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        checkpoint = args.get("checkpoint", 0)
        message = args.get("message", "")

        if not self._snapshot:
            return ToolResult(output="", error="No snapshot manager available")

        snap = self._snapshot.get_snapshot(checkpoint)
        if not snap:
            available = [s.turn for s in self._snapshot.list_snapshots()]
            return ToolResult(
                output="",
                error=f"No snapshot at turn {checkpoint}. Available: {available}",
            )

        # Sync wrapper: actual revert is async.
        # The caller (AgentLoop) should handle conversation truncation.
        return ToolResult(
            output=f"Rollback requested to turn {checkpoint}.",
            metadata={
                "rollback_turn": checkpoint,
                "rollback_message": message,
                "files_to_revert": snap.modified_files,
            },
        )

    async def async_execute(
        self, args: dict[str, Any], env: Environment | None,
    ) -> ToolResult:
        checkpoint = args.get("checkpoint", 0)
        message = args.get("message", "")

        if not self._snapshot:
            return ToolResult(output="", error="No snapshot manager available")

        snap = self._snapshot.get_snapshot(checkpoint)
        if not snap:
            available = [s.turn for s in self._snapshot.list_snapshots()]
            return ToolResult(
                output="",
                error=f"No snapshot at turn {checkpoint}. Available: {available}",
            )

        # Actually revert files
        restored = await self._snapshot.revert(to_turn=checkpoint)

        return ToolResult(
            output=f"Rolled back to turn {checkpoint}. Restored {len(restored)} files: {', '.join(restored)}",
            metadata={
                "rollback_turn": checkpoint,
                "rollback_message": message,
                "files_restored": restored,
            },
        )
