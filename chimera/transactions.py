"""Multi-file edit transactions with rollback support."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from chimera.types import ChangeType, FileChange

if TYPE_CHECKING:
    from chimera.env.base import Environment


class TransactionState(Enum):
    """State of a file transaction."""

    OPEN = "open"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"


@dataclass
class StagedChange:
    """A single staged file operation within a transaction."""

    path: str
    change_type: ChangeType
    content: str | None  # None for DELETE
    original_content: str | None = None


class FileTransaction:
    """Atomic multi-file edit transaction with rollback.

    Groups multiple file writes/deletes into an atomic unit. On commit,
    a checkpoint is taken first so that all changes can be rolled back
    if any write fails or if explicit rollback is requested.

    Example:
        ```python
        tx = FileTransaction(env)
        tx.stage_write("a.py", "print('hello')")
        tx.stage_write("b.py", "print('world')")
        changes = tx.commit()
        # If something goes wrong:
        tx.rollback()
        ```
    """

    def __init__(self, env: Environment) -> None:
        self._env = env
        self._changes: dict[str, StagedChange] = {}
        self._state = TransactionState.OPEN
        self._checkpoint_id: str | None = None

    def stage_write(self, path: str, content: str) -> None:
        """Stage a file write (create or edit).

        Args:
            path: Workspace-relative file path.
            content: New file content.

        Raises:
            RuntimeError: If transaction is not open.
        """
        if self._state != TransactionState.OPEN:
            raise RuntimeError(f"Cannot stage changes in {self._state.value} transaction")
        try:
            original = self._env.read_file(path)
            change_type = ChangeType.EDIT
        except FileNotFoundError:
            original = None
            change_type = ChangeType.CREATE
        self._changes[path] = StagedChange(
            path=path, change_type=change_type, content=content, original_content=original
        )

    def stage_delete(self, path: str) -> None:
        """Stage a file deletion.

        Args:
            path: Workspace-relative file path.

        Raises:
            FileNotFoundError: If the file does not exist.
            RuntimeError: If transaction is not open.
        """
        if self._state != TransactionState.OPEN:
            raise RuntimeError(f"Cannot stage changes in {self._state.value} transaction")
        original = self._env.read_file(path)  # raises FileNotFoundError if missing
        self._changes[path] = StagedChange(
            path=path, change_type=ChangeType.DELETE, content=None, original_content=original
        )

    def preview(self) -> list[FileChange]:
        """Preview staged changes without applying them.

        Returns:
            List of FileChange objects with computed diffs.
        """
        result = []
        for change in self._changes.values():
            diff = None
            if change.change_type == ChangeType.EDIT and change.original_content is not None and change.content is not None:
                diff = FileChange.compute_diff(change.path, change.original_content, change.content)
            elif change.change_type == ChangeType.CREATE and change.content is not None:
                diff = FileChange.compute_diff(change.path, "", change.content)
            elif change.change_type == ChangeType.DELETE and change.original_content is not None:
                diff = FileChange.compute_diff(change.path, change.original_content, "")
            result.append(FileChange(
                path=change.path,
                change_type=change.change_type,
                before_content=change.original_content,
                after_content=change.content,
                diff=diff,
            ))
        return result

    def commit(self) -> list[FileChange]:
        """Apply all staged changes atomically.

        Takes a checkpoint before applying changes. If any write fails,
        the checkpoint is automatically restored.

        Returns:
            List of FileChange objects describing what was applied.

        Raises:
            RuntimeError: If no changes staged or transaction not open.
        """
        if self._state != TransactionState.OPEN:
            raise RuntimeError(f"Cannot commit {self._state.value} transaction")
        if not self._changes:
            raise RuntimeError("No changes staged")

        changes = self.preview()
        self._checkpoint_id = self._env.checkpoint()
        try:
            for change in self._changes.values():
                if change.change_type == ChangeType.DELETE:
                    self._env.write_file(change.path, "")  # Mark as deleted
                else:
                    assert change.content is not None
                    self._env.write_file(change.path, change.content)
        except Exception:
            self._env.restore(self._checkpoint_id)
            self._checkpoint_id = None
            raise

        self._state = TransactionState.COMMITTED
        return changes

    def rollback(self) -> None:
        """Rollback a committed transaction.

        Raises:
            RuntimeError: If transaction was not committed.
        """
        if self._state != TransactionState.COMMITTED:
            raise RuntimeError("Can only rollback a committed transaction")
        if self._checkpoint_id is None:
            raise RuntimeError("No checkpoint available")
        self._env.restore(self._checkpoint_id)
        self._state = TransactionState.ROLLED_BACK

    def __enter__(self) -> FileTransaction:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: object) -> None:
        if exc_type is not None and self._state == TransactionState.COMMITTED:
            self.rollback()
