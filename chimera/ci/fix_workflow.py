"""CI fix workflow: diagnose and fix CI failures."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from chimera.ci.failure_parser import FailureInfo, parse_ci_log

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.types import AgentResult


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

    def run(
        self,
        log: str,
        agent: Agent,
        env: Environment,
        verify: Callable[[], tuple[bool, str]] | None = None,
    ) -> bool:
        """Diagnose CI failures and attempt to fix them using the agent.

        Args:
            log: Raw CI log output to parse for failures.
            agent: Agent to use for generating fixes.
            env: Environment for the agent to execute in.
            verify: Optional callable invoked after each fix attempt to check
                whether the CI is now green. Returns ``(passed, new_log)``.
                If ``passed`` is True, ``run`` returns True immediately.
                If ``passed`` is False, ``new_log`` is re-diagnosed and a
                fresh prompt is built from the new failures.
                When ``verify`` is None, ``max_attempts > 1`` cannot re-check
                — the loop relies solely on ``AgentResult.success``, which
                only indicates the ReAct loop finished, not that the CI is
                actually fixed.

        Returns:
            True if any attempt succeeded, False otherwise.
        """
        failures = self.diagnose(log)
        if not failures:
            return True

        for _ in range(self._max_attempts):
            prompt = self.build_prompt(failures)
            result: AgentResult = agent.run(prompt, env)
            self.record_attempt(
                failures=failures,
                prompt=prompt,
                success=result.success,
                cost=result.cost,
            )

            # If a verify callback was provided, use it as the authoritative
            # signal instead of the agent's own success flag.
            if verify is not None:
                passed, new_log = verify()
                if passed:
                    # Overwrite the last attempt's success field so
                    # ``succeeded`` reflects verified state.
                    self._attempts[-1].success = True
                    return True
                # CI still red; re-diagnose so the next prompt targets the
                # remaining failures, not the original ones.
                self._attempts[-1].success = False
                failures = self.diagnose(new_log) or failures
            elif result.success:
                return True

            if self._budget is not None and self.total_cost >= self._budget:
                break

        return self.succeeded
