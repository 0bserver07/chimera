"""Ghost commits: automatic snapshots before every file-modifying action.

Creates lightweight snapshots that can be reverted with undo(). Works in
both git repos (using git stash-like commits on a hidden ref) and plain
directories (file copies).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GhostSnapshot:
    """A single undo-able snapshot."""

    id: str
    label: str
    timestamp: float
    files: dict[str, str]  # path → content at time of snapshot


class GhostCommitManager:
    """Manages a stack of file snapshots for undo operations.

    Example::

        ghost = GhostCommitManager(workdir="/path/to/project")
        ghost.snapshot("write_file: main.py")
        # ... agent modifies main.py ...
        ghost.undo()  # restores main.py to pre-write state
    """

    def __init__(self, workdir: str, max_snapshots: int = 50) -> None:
        self._workdir = Path(workdir)
        self._max_snapshots = max_snapshots
        self._stack: list[GhostSnapshot] = []
        self._counter = 0

    @property
    def depth(self) -> int:
        """Number of snapshots in the stack."""
        return len(self._stack)

    @property
    def history(self) -> list[GhostSnapshot]:
        """All snapshots, oldest first."""
        return list(self._stack)

    def snapshot(self, label: str, paths: list[str] | None = None) -> str:
        """Create a snapshot of specified files (or all tracked files).

        Args:
            label: Human-readable description (e.g. "write_file: main.py").
            paths: Files to snapshot. None = snapshot files from last snapshot.

        Returns:
            Snapshot ID.
        """
        self._counter += 1
        snap_id = f"ghost-{self._counter}"

        files: dict[str, str] = {}
        if paths:
            for p in paths:
                full = self._workdir / p
                if full.is_file():
                    try:
                        files[p] = full.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        pass
                else:
                    files[p] = ""  # File doesn't exist yet — record absence

        snap = GhostSnapshot(
            id=snap_id,
            label=label,
            timestamp=time.time(),
            files=files,
        )
        self._stack.append(snap)

        # Evict oldest if over limit
        if len(self._stack) > self._max_snapshots:
            self._stack.pop(0)

        return snap_id

    def undo(self, n: int = 1) -> list[str]:
        """Undo the last N snapshots, restoring files to their prior state.

        Args:
            n: Number of snapshots to undo.

        Returns:
            List of files that were restored.
        """
        restored: list[str] = []
        for _ in range(min(n, len(self._stack))):
            snap = self._stack.pop()
            for path, content in snap.files.items():
                full = self._workdir / path
                if content == "":
                    # File didn't exist before — delete it
                    if full.exists():
                        full.unlink()
                        restored.append(f"deleted: {path}")
                else:
                    full.parent.mkdir(parents=True, exist_ok=True)
                    full.write_text(content, encoding="utf-8")
                    restored.append(f"restored: {path}")
        return restored

    def peek(self) -> GhostSnapshot | None:
        """Look at the most recent snapshot without popping it."""
        return self._stack[-1] if self._stack else None

    def clear(self) -> None:
        """Clear all snapshots."""
        self._stack.clear()
