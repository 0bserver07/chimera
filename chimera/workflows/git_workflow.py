"""Git-aware workflow: auto-branching, diff context, commit strategies."""
from __future__ import annotations

import uuid
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.env.git_env import GitEnvironment


class CommitStrategy(Enum):
    PER_STEP = "per_step"      # commit after each agent step
    PER_TASK = "per_task"      # commit when task completes
    MANUAL = "manual"          # only commit on explicit request


class GitWorkflow:
    """Manages git branching, diff context, and commits for agent tasks."""

    def __init__(self, env: GitEnvironment, strategy: CommitStrategy = CommitStrategy.PER_TASK) -> None:
        self._env = env
        self._strategy = strategy
        self._branch_name: str | None = None
        self._original_branch: str | None = None

    @property
    def branch_name(self) -> str | None:
        return self._branch_name

    @property
    def strategy(self) -> CommitStrategy:
        return self._strategy

    def start(self, task_name: str | None = None) -> str:
        """Create and checkout a feature branch. Returns branch name."""
        # Get current branch
        result = self._env._git("rev-parse --abbrev-ref HEAD")
        self._original_branch = result.stdout.strip()

        # Generate branch name
        suffix = task_name or uuid.uuid4().hex[:8]
        self._branch_name = f"chimera/{suffix}"

        # Create and checkout branch
        self._env._git(f"checkout -b {self._branch_name}")
        return self._branch_name

    def get_diff_context(self) -> str:
        """Get current git diff as context for the agent."""
        staged = self._env._git("diff --cached")
        unstaged = self._env._git("diff")
        parts = []
        if staged.stdout.strip():
            parts.append(f"=== Staged Changes ===\n{staged.stdout.strip()}")
        if unstaged.stdout.strip():
            parts.append(f"=== Unstaged Changes ===\n{unstaged.stdout.strip()}")
        return "\n\n".join(parts) if parts else ""

    def get_changed_files(self) -> list[str]:
        """Get list of files changed since branch creation."""
        if not self._original_branch:
            return []
        result = self._env._git(f"diff --name-only {self._original_branch}")
        return [f for f in result.stdout.strip().split("\n") if f]

    def commit(self, message: str) -> str:
        """Stage all and commit. Returns commit SHA."""
        self._env._git("add -A")
        self._env._git(f'commit -m "{message}" --allow-empty')
        result = self._env._git("rev-parse HEAD")
        return result.stdout.strip()

    def finish(self, merge: bool = True) -> str | None:
        """Complete the workflow. Optionally merge back to original branch.

        Returns merge commit SHA or None.
        """
        if not self._original_branch or not self._branch_name:
            return None

        # Commit any pending changes
        status = self._env._git("status --porcelain")
        if status.stdout.strip():
            self.commit("chore: final changes before merge")

        if merge:
            self._env._git(f"checkout {self._original_branch}")
            self._env._git(f"merge {self._branch_name}")
            # Clean up branch
            self._env._git(f"branch -d {self._branch_name}")
            result = self._env._git("rev-parse HEAD")
            self._branch_name = None
            return result.stdout.strip()

        self._branch_name = None
        return None

    def abort(self) -> None:
        """Abort the workflow, discard changes, return to original branch."""
        if self._original_branch:
            self._env._git(f"checkout {self._original_branch}")
            if self._branch_name:
                self._env._git(f"branch -D {self._branch_name}")
            self._branch_name = None
