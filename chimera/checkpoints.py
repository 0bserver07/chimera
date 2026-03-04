"""Checkpoint manager: named checkpoints with metadata."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.env.base import Environment


@dataclass
class CheckpointInfo:
    """Metadata for a saved checkpoint."""
    id: str
    name: str
    timestamp: float
    description: str = ""

    @property
    def time_str(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))


class CheckpointManager:
    """Manages named checkpoints with metadata on top of Environment.checkpoint/restore.

    Provides a user-friendly layer over the raw checkpoint IDs.
    """

    def __init__(self, env: Environment) -> None:
        self._env = env
        self._checkpoints: list[CheckpointInfo] = []
        self._auto_checkpoint: bool = False

    @property
    def auto_checkpoint(self) -> bool:
        return self._auto_checkpoint

    @auto_checkpoint.setter
    def auto_checkpoint(self, value: bool) -> None:
        self._auto_checkpoint = value

    def create(self, name: str = "", description: str = "") -> CheckpointInfo:
        """Create a named checkpoint.

        Args:
            name: Human-readable name. Auto-generated if empty.
            description: Optional description.

        Returns:
            CheckpointInfo with the checkpoint metadata.
        """
        checkpoint_id = self._env.checkpoint()
        if not name:
            name = f"checkpoint-{len(self._checkpoints) + 1}"
        info = CheckpointInfo(
            id=checkpoint_id,
            name=name,
            timestamp=time.time(),
            description=description,
        )
        self._checkpoints.append(info)
        return info

    def restore_by_name(self, name: str) -> CheckpointInfo:
        """Restore to a checkpoint by name.

        Raises:
            KeyError: If no checkpoint with that name exists.
        """
        for cp in reversed(self._checkpoints):
            if cp.name == name:
                self._env.restore(cp.id)
                return cp
        raise KeyError(f"No checkpoint named {name!r}")

    def restore_by_id(self, checkpoint_id: str) -> CheckpointInfo:
        """Restore to a checkpoint by its raw ID.

        Raises:
            KeyError: If checkpoint ID not found.
        """
        for cp in self._checkpoints:
            if cp.id == checkpoint_id:
                self._env.restore(cp.id)
                return cp
        raise KeyError(f"No checkpoint with ID {checkpoint_id!r}")

    def undo(self) -> CheckpointInfo | None:
        """Restore to the most recent checkpoint. Returns None if no checkpoints."""
        if not self._checkpoints:
            return None
        latest = self._checkpoints[-1]
        self._env.restore(latest.id)
        return latest

    def list_checkpoints(self) -> list[CheckpointInfo]:
        """Return all checkpoints in creation order."""
        return list(self._checkpoints)

    def get(self, name: str) -> CheckpointInfo | None:
        """Get checkpoint info by name without restoring."""
        for cp in reversed(self._checkpoints):
            if cp.name == name:
                return cp
        return None

    def clear(self) -> None:
        """Clear all checkpoint records (does not restore anything)."""
        self._checkpoints.clear()
