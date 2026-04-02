from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.types import Message


@dataclass
class Branch:
    """A branch in the session tree."""

    branch_id: str
    parent_branch_id: str | None
    fork_point: int  # Message index where this branch diverges
    messages: list[Message]
    created_at: float = field(default_factory=time.time)
    name: str = ""


@dataclass
class SessionTree:
    """Tree-structured session with branching support."""

    branches: dict[str, Branch] = field(default_factory=dict)
    active_branch_id: str = "main"

    def __post_init__(self) -> None:
        if "main" not in self.branches:
            self.branches["main"] = Branch(
                branch_id="main",
                parent_branch_id=None,
                fork_point=0,
                messages=[],
                name="main",
            )

    @property
    def active_branch(self) -> Branch:
        return self.branches[self.active_branch_id]

    @property
    def messages(self) -> list[Message]:
        """Get messages for the active branch (including inherited from parent)."""
        return self._get_full_messages(self.active_branch_id)

    def _get_full_messages(self, branch_id: str) -> list[Message]:
        branch = self.branches[branch_id]
        if branch.parent_branch_id is None:
            return list(branch.messages)
        # Get parent messages up to fork point, then this branch's messages
        parent_msgs = self._get_full_messages(branch.parent_branch_id)
        return parent_msgs[: branch.fork_point] + branch.messages

    def add_message(self, message: Message) -> None:
        """Add a message to the active branch."""
        self.active_branch.messages.append(message)

    def fork(self, at_message: int | None = None, name: str = "") -> str:
        """Fork a new branch from the active branch at a specific message index.

        Returns the new branch ID.
        """
        import uuid

        branch_id = f"branch_{uuid.uuid4().hex[:8]}"
        fork_point = at_message if at_message is not None else len(self.messages)

        self.branches[branch_id] = Branch(
            branch_id=branch_id,
            parent_branch_id=self.active_branch_id,
            fork_point=fork_point,
            messages=[],
            name=name or branch_id,
        )
        self.active_branch_id = branch_id
        return branch_id

    def switch(self, branch_id: str) -> bool:
        """Switch to a different branch."""
        if branch_id in self.branches:
            self.active_branch_id = branch_id
            return True
        return False

    def list_branches(self) -> list[dict[str, Any]]:
        """List all branches with metadata."""
        result = []
        for b in self.branches.values():
            full_msgs = self._get_full_messages(b.branch_id)
            result.append(
                {
                    "id": b.branch_id,
                    "name": b.name,
                    "parent": b.parent_branch_id,
                    "fork_point": b.fork_point,
                    "message_count": len(full_msgs),
                    "active": b.branch_id == self.active_branch_id,
                }
            )
        return result

    def tree_view(self) -> str:
        """ASCII tree view of all branches."""
        lines: list[str] = []
        self._render_tree("main", lines, "", True)
        return "\n".join(lines)

    def _render_tree(
        self, branch_id: str, lines: list[str], prefix: str, is_last: bool
    ) -> None:
        branch = self.branches[branch_id]
        marker = "* " if branch_id == self.active_branch_id else "  "
        connector = "└── " if is_last else "├── "
        full_msgs = self._get_full_messages(branch_id)
        lines.append(
            f"{prefix}{connector}{marker}{branch.name} ({len(full_msgs)} msgs)"
        )

        children = [
            b for b in self.branches.values() if b.parent_branch_id == branch_id
        ]
        for i, child in enumerate(children):
            ext = "    " if is_last else "│   "
            self._render_tree(
                child.branch_id, lines, prefix + ext, i == len(children) - 1
            )
