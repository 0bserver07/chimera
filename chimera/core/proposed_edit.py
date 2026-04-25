"""Diff proposal workflow: stage edits for user review before applying.

Provides a ProposedEdit system where the agent generates changes that are
staged (not applied) until reviewed. Users can accept all, reject all,
or accept/reject individual edits.

Implements a stage-then-review apply pattern for tool-driven edits.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EditStatus(Enum):
    """Status of a proposed edit."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass
class ProposedEdit:
    """A single proposed file change.

    Args:
        path: File path to modify.
        original: Original file content (empty string for new files).
        proposed: Proposed new content.
        description: Human-readable description of the change.
    """

    path: str
    original: str
    proposed: str
    description: str = ""
    status: EditStatus = EditStatus.PENDING

    @property
    def is_new_file(self) -> bool:
        """Whether this creates a new file."""
        return self.original == ""

    @property
    def is_deletion(self) -> bool:
        """Whether this deletes a file."""
        return self.proposed == "" and self.original != ""

    def unified_diff(self, context_lines: int = 3) -> str:
        """Generate a unified diff string."""
        original_lines = self.original.splitlines(keepends=True)
        proposed_lines = self.proposed.splitlines(keepends=True)
        diff = difflib.unified_diff(
            original_lines,
            proposed_lines,
            fromfile=f"a/{self.path}",
            tofile=f"b/{self.path}",
            n=context_lines,
        )
        return "".join(diff)

    def stat(self) -> dict[str, int]:
        """Return additions/deletions counts."""
        diff = self.unified_diff()
        additions = sum(1 for line in diff.split("\n") if line.startswith("+") and not line.startswith("+++"))
        deletions = sum(1 for line in diff.split("\n") if line.startswith("-") and not line.startswith("---"))
        return {"additions": additions, "deletions": deletions}


@dataclass
class EditProposal:
    """A collection of proposed edits for review.

    Example::

        proposal = EditProposal()
        proposal.add("calc.py", old_content, new_content, "Fix add function")
        proposal.add("test.py", "", test_content, "Add unit tests")

        # Review
        print(proposal.summary())
        for edit in proposal.edits:
            print(edit.unified_diff())

        # Accept all
        proposal.accept_all()
        applied = proposal.apply(env)
    """

    edits: list[ProposedEdit] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add(
        self,
        path: str,
        original: str,
        proposed: str,
        description: str = "",
    ) -> ProposedEdit:
        """Add a proposed edit."""
        edit = ProposedEdit(
            path=path, original=original, proposed=proposed, description=description,
        )
        self.edits.append(edit)
        return edit

    def accept_all(self) -> None:
        """Accept all pending edits."""
        for edit in self.edits:
            if edit.status == EditStatus.PENDING:
                edit.status = EditStatus.ACCEPTED

    def reject_all(self) -> None:
        """Reject all pending edits."""
        for edit in self.edits:
            if edit.status == EditStatus.PENDING:
                edit.status = EditStatus.REJECTED

    def accept(self, index: int) -> None:
        """Accept a specific edit by index."""
        self.edits[index].status = EditStatus.ACCEPTED

    def reject(self, index: int) -> None:
        """Reject a specific edit by index."""
        self.edits[index].status = EditStatus.REJECTED

    @property
    def pending(self) -> list[ProposedEdit]:
        """Edits still awaiting review."""
        return [e for e in self.edits if e.status == EditStatus.PENDING]

    @property
    def accepted(self) -> list[ProposedEdit]:
        """Edits that have been accepted."""
        return [e for e in self.edits if e.status == EditStatus.ACCEPTED]

    def summary(self) -> str:
        """Human-readable summary of all proposed edits."""
        lines: list[str] = [f"Proposed {len(self.edits)} edit(s):"]
        for i, edit in enumerate(self.edits):
            stat = edit.stat()
            status = edit.status.value.upper()
            kind = "new" if edit.is_new_file else "del" if edit.is_deletion else "mod"
            lines.append(
                f"  [{status}] {i}. {edit.path} ({kind}) "
                f"+{stat['additions']}/-{stat['deletions']}"
                f"{': ' + edit.description if edit.description else ''}"
            )
        return "\n".join(lines)

    def apply(self, env: Any) -> list[str]:
        """Apply all accepted edits to an environment.

        Args:
            env: An Environment instance with write_file().

        Returns:
            List of file paths that were written.
        """
        applied: list[str] = []
        for edit in self.accepted:
            env.write_file(edit.path, edit.proposed)
            applied.append(edit.path)
        return applied

    def full_diff(self) -> str:
        """Combined unified diff for all edits."""
        return "\n".join(edit.unified_diff() for edit in self.edits)
