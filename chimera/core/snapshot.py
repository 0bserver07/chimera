"""Snapshot system for file state tracking and undo/revert.

Records file contents at each turn boundary so users can revert
agent changes to any prior state. This is the #1 safety feature
for a coding agent.

v1 implementation: in-memory content storage with SHA-256 hashing.
Git plumbing optimization deferred to a later phase.
"""
from __future__ import annotations

import difflib
import hashlib
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["FileState", "Snapshot", "SnapshotManager"]


@dataclass
class FileState:
    """State of a single file at a snapshot point."""

    content_hash: str
    content: bytes | None  # None means file was deleted
    size: int


@dataclass
class Snapshot:
    """A snapshot of file states at a particular turn."""

    turn: int
    timestamp: float
    modified_files: list[str]
    file_states: dict[str, FileState] = field(default_factory=dict)


class SnapshotManager:
    """Track file state per turn for undo/revert.

    Stores file contents directly in memory (dict[str, bytes]).
    Uses hashlib.sha256 for content hashing.

    Usage::

        mgr = SnapshotManager(project_dir)
        await mgr.take(turn=1, modified_files=["foo.py"])
        # ... agent modifies foo.py ...
        await mgr.take(turn=2, modified_files=["foo.py"])
        await mgr.revert(to_turn=1)  # restores foo.py to turn 1
    """

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = project_dir.resolve()
        self._snapshots: list[Snapshot] = []
        self._git_available = self._check_git()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def take(self, turn: int, modified_files: list[str]) -> Snapshot:
        """Take a snapshot of the current file state for the given turn.

        For each file in *modified_files*, reads content from disk and
        computes a SHA-256 hash.  If the file has been deleted, stores
        ``content=None``.
        """
        file_states: dict[str, FileState] = {}

        for rel_path in modified_files:
            abs_path = self._project_dir / rel_path
            if abs_path.exists():
                content = abs_path.read_bytes()
                content_hash = hashlib.sha256(content).hexdigest()
                file_states[rel_path] = FileState(
                    content_hash=content_hash,
                    content=content,
                    size=len(content),
                )
            else:
                # File was deleted
                file_states[rel_path] = FileState(
                    content_hash="",
                    content=None,
                    size=0,
                )

        snap = Snapshot(
            turn=turn,
            timestamp=time.time(),
            modified_files=list(modified_files),
            file_states=file_states,
        )
        self._snapshots.append(snap)
        return snap

    async def diff(self, from_turn: int, to_turn: int | None = None) -> str:
        """Compute unified diff between two snapshots.

        If *to_turn* is ``None``, diffs the snapshot against the current
        working tree.
        """
        from_snap = self.get_snapshot(from_turn)
        if from_snap is None:
            return ""

        to_snap = self.get_snapshot(to_turn) if to_turn is not None else None

        # Collect all file paths across both snapshots
        all_paths: set[str] = set(from_snap.file_states.keys())
        if to_snap is not None:
            all_paths |= set(to_snap.file_states.keys())

        diff_parts: list[str] = []

        for rel_path in sorted(all_paths):
            from_state = from_snap.file_states.get(rel_path)
            from_lines = self._state_to_lines(from_state)

            if to_snap is not None:
                to_state = to_snap.file_states.get(rel_path)
                to_lines = self._state_to_lines(to_state)
            else:
                # Diff against current working tree
                abs_path = self._project_dir / rel_path
                if abs_path.exists():
                    to_lines = abs_path.read_text(errors="replace").splitlines(
                        keepends=True,
                    )
                else:
                    to_lines = []

            diff_lines = list(
                difflib.unified_diff(
                    from_lines,
                    to_lines,
                    fromfile=f"a/{rel_path}",
                    tofile=f"b/{rel_path}",
                ),
            )
            if diff_lines:
                diff_parts.append("".join(diff_lines))

        return "\n".join(diff_parts)

    async def revert(self, to_turn: int) -> list[str]:
        """Restore ALL files to their state at the given turn.

        Returns the list of file paths that were restored.
        """
        snap = self.get_snapshot(to_turn)
        if snap is None:
            return []

        restored: list[str] = []
        for rel_path, state in snap.file_states.items():
            abs_path = self._project_dir / rel_path
            if state.content is not None:
                # Ensure parent directory exists
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_bytes(state.content)
            else:
                # File was deleted at this turn — remove it if it exists
                if abs_path.exists():
                    abs_path.unlink()
            restored.append(rel_path)

        return restored

    async def revert_file(self, path: str, to_turn: int) -> bool:
        """Restore a single file to its state at the given turn.

        Returns ``True`` if the file was found in the snapshot and restored,
        ``False`` otherwise.
        """
        snap = self.get_snapshot(to_turn)
        if snap is None:
            return False

        state = snap.file_states.get(path)
        if state is None:
            return False

        abs_path = self._project_dir / path
        if state.content is not None:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_bytes(state.content)
        else:
            if abs_path.exists():
                abs_path.unlink()

        return True

    def list_snapshots(self) -> list[Snapshot]:
        """Return all snapshots ordered by turn."""
        return list(self._snapshots)

    def get_snapshot(self, turn: int) -> Snapshot | None:
        """Return the snapshot for a specific turn, or None."""
        for snap in self._snapshots:
            if snap.turn == turn:
                return snap
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_git() -> bool:
        """Check whether git is available on this system."""
        return shutil.which("git") is not None

    @staticmethod
    def _state_to_lines(state: FileState | None) -> list[str]:
        """Convert a FileState to a list of text lines for diffing."""
        if state is None or state.content is None:
            return []
        return state.content.decode("utf-8", errors="replace").splitlines(
            keepends=True,
        )
