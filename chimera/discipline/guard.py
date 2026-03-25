"""Behavioral constraint guards for agent discipline.

Guards are advisory by default (severity="warning" logs only).
Only severity="block" raises :class:`DisciplineViolation`.

Guards are fast and deterministic -- no LLM calls.
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


__all__ = [
    "DisciplineGuard",
    "DisciplineViolation",
    "DepthGuard",
    "GuardResult",
    "RetryBudgetGuard",
    "ScopeGuard",
    "VerificationGuard",
]

# Actions considered read/search (exploration).
_READ_ACTIONS = frozenset({
    "read_file", "grep", "glob", "search", "list_files", "repo_map",
})

# Actions considered write/modify (commitment).
_WRITE_ACTIONS = frozenset({
    "write_file", "edit_file", "replace_in_file", "bash",
})


@dataclass
class GuardResult:
    """Result of a discipline guard check.

    Attributes:
        allowed: Whether the action is permitted.
        reason: Human-readable explanation (empty when allowed).
        severity: ``"warning"`` logs only; ``"block"`` raises
            :class:`DisciplineViolation`.
    """

    allowed: bool
    reason: str = ""
    severity: str = "warning"


class DisciplineViolation(Exception):
    """Raised when a guard with severity='block' denies an action."""

    def __init__(self, guard_name: str, reason: str) -> None:
        self.guard_name = guard_name
        self.reason = reason
        super().__init__(f"[{guard_name}] {reason}")


class DisciplineGuard(ABC):
    """ABC for behavioral constraints.

    Subclasses must implement :meth:`check` which receives an action name
    and a context dict (contents vary by guard).
    """

    name: str = ""

    @abstractmethod
    def check(self, action: str, context: dict[str, Any]) -> GuardResult:
        """Evaluate whether *action* is allowed given *context*.

        Args:
            action: Tool / operation name (e.g. ``"write_file"``).
            context: Contextual data -- keys vary by guard.

        Returns:
            A :class:`GuardResult` indicating the verdict.
        """


class ScopeGuard(DisciplineGuard):
    """Flag changes to files not in the task scope.

    ``context`` must contain a ``"file_path"`` key for write/edit actions.
    ``task_files`` is the set of files the task is expected to touch.
    If ``task_files`` is ``None``, the guard always allows (nothing to check).
    """

    name = "scope"

    def __init__(
        self,
        task_files: set[str] | None = None,
        *,
        severity: str = "warning",
    ) -> None:
        self._task_files = task_files
        self._severity = severity

    def check(self, action: str, context: dict[str, Any]) -> GuardResult:
        """If action is write/edit and file_path not in task_files, flag it."""
        if self._task_files is None:
            return GuardResult(allowed=True)

        if action not in _WRITE_ACTIONS:
            return GuardResult(allowed=True)

        file_path = context.get("file_path", "")
        if not file_path:
            return GuardResult(allowed=True)

        if file_path in self._task_files:
            return GuardResult(allowed=True)

        return GuardResult(
            allowed=False,
            reason=f"File '{file_path}' is outside task scope",
            severity=self._severity,
        )


class DepthGuard(DisciplineGuard):
    """Limit consecutive read/search without writes.

    Prevents rabbit-hole exploration.  After *max_depth* consecutive
    read/grep/glob operations, suggests committing to an approach.
    Resets counter on write/edit/bash operations.
    """

    name = "depth"

    def __init__(self, max_depth: int = 10) -> None:
        self._max_depth = max_depth
        self._consecutive_reads = 0

    def check(self, action: str, context: dict[str, Any]) -> GuardResult:
        if action in _WRITE_ACTIONS:
            self._consecutive_reads = 0
            return GuardResult(allowed=True)

        if action in _READ_ACTIONS:
            self._consecutive_reads += 1
            if self._consecutive_reads > self._max_depth:
                return GuardResult(
                    allowed=False,
                    reason=(
                        f"{self._consecutive_reads} consecutive reads without a write "
                        f"(limit: {self._max_depth}). Consider committing to an approach."
                    ),
                    severity="warning",
                )

        return GuardResult(allowed=True)


class VerificationGuard(DisciplineGuard):
    """Require test execution before completion.

    Checks that at least one test-related tool call occurred
    (bash with pytest/test command) before allowing a ``"done"`` signal.
    """

    name = "verification"

    def __init__(self) -> None:
        self._tests_ran = False

    def check(self, action: str, context: dict[str, Any]) -> GuardResult:
        # Detect test execution.
        if action == "bash":
            command = context.get("command", "")
            if any(kw in command for kw in ("pytest", "test", "unittest", "nose")):
                self._tests_ran = True

        # Check at completion time.
        if action == "done":
            if not self._tests_ran:
                return GuardResult(
                    allowed=False,
                    reason="No test execution detected before completion",
                    severity="warning",
                )

        return GuardResult(allowed=True)


class RetryBudgetGuard(DisciplineGuard):
    """Limit retries on the same approach.

    Tracks edit signatures (hash of file_path + change summary).
    After *max_retries* similar edits to the same file, forces
    a different approach or escalation.
    """

    name = "retry_budget"

    def __init__(self, max_retries: int = 3) -> None:
        self._max_retries = max_retries
        self._edit_counts: dict[str, int] = {}

    def check(self, action: str, context: dict[str, Any]) -> GuardResult:
        if action not in ("edit_file", "write_file", "replace_in_file"):
            return GuardResult(allowed=True)

        file_path = context.get("file_path", "")
        change = context.get("change", "")
        sig = hashlib.sha256(f"{file_path}:{change}".encode()).hexdigest()[:16]

        self._edit_counts[sig] = self._edit_counts.get(sig, 0) + 1
        count = self._edit_counts[sig]

        if count > self._max_retries:
            return GuardResult(
                allowed=False,
                reason=(
                    f"Same edit to '{file_path}' attempted {count} times "
                    f"(limit: {self._max_retries}). Try a different approach."
                ),
                severity="warning",
            )

        return GuardResult(allowed=True)
