"""CI fix workflow: diagnose and fix CI failures."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from chimera.ci.failure_parser import FailureInfo, parse_ci_log

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment


@dataclass
class FixAttempt:
    """Record of a single fix attempt."""
    failures: list[FailureInfo]
    prompt: str
    success: bool = False
    cost: float = 0.0
    error: str = ""


class CIFixWorkflow:
    """Workflow for diagnosing and fixing CI failures.

    Takes a CI log, parses failures, generates a prompt for the agent,
    and tracks fix attempts.
    """

    def __init__(self, max_attempts: int = 3, budget: float | None = None) -> None:
        self._max_attempts = max_attempts
        self._budget = budget
        self._attempts: list[FixAttempt] = []

    def diagnose(self, log: str) -> list[FailureInfo]:
        """Parse CI log and return structured failure info."""
        return parse_ci_log(log)

    def build_prompt(self, failures: list[FailureInfo], context: str = "") -> str:
        """Build an agent prompt from parsed failures.

        Args:
            failures: Parsed failure information.
            context: Additional context (repo description, etc.)
        """
        parts = ["Fix the following CI failures:\n"]
        for i, f in enumerate(failures, 1):
            parts.append(f"{i}. {f.summary}")
        if context:
            parts.append(f"\nContext: {context}")
        parts.append("\nDiagnose the root cause, make minimal changes, and verify the fix by running tests.")
        return "\n".join(parts)

    @property
    def attempts(self) -> list[FixAttempt]:
        return list(self._attempts)

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    @property
    def total_cost(self) -> float:
        return sum(a.cost for a in self._attempts)

    @property
    def succeeded(self) -> bool:
        return any(a.success for a in self._attempts)

    def record_attempt(self, failures: list[FailureInfo], prompt: str,
                       success: bool = False, cost: float = 0.0, error: str = "") -> FixAttempt:
        """Record a fix attempt."""
        attempt = FixAttempt(
            failures=failures,
            prompt=prompt,
            success=success,
            cost=cost,
            error=error,
        )
        self._attempts.append(attempt)
        return attempt
