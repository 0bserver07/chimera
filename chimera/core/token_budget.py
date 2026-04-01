"""Token budget enforcement for agent loops.

Tracks token usage against a budget and decides whether the agent should
continue, receive a nudge to wrap up, or stop outright.

Constants:
    COMPLETION_THRESHOLD: Usage fraction (0.9 = 90 %) at which the budget
        is considered exhausted.
    DIMINISHING_THRESHOLD: If a turn produces fewer than this many output
        tokens it is counted as "low output."  Three consecutive low-output
        turns trigger a stop.
"""

from __future__ import annotations

from dataclasses import dataclass

COMPLETION_THRESHOLD = 0.9  # 90%
DIMINISHING_THRESHOLD = 500  # tokens


@dataclass
class TokenBudgetResult:
    """Result of a budget check.

    Attributes:
        should_continue: ``True`` if the loop may proceed.
        reason: Machine-readable reason (``"ok"``, ``"budget_low"``,
            ``"budget_exhausted"``, ``"diminishing_returns"``).
        nudge_message: Optional human-readable nudge to inject into
            the conversation when the budget is running low.
    """

    should_continue: bool
    reason: str
    nudge_message: str | None = None


class TokenBudget:
    """Track token usage against a budget.  Nudge model to continue or stop.

    Args:
        budget_tokens: Total token budget for the session/task.
    """

    def __init__(self, budget_tokens: int) -> None:
        self.budget = budget_tokens
        self.used = 0
        self._continuation_count = 0

    def record(self, tokens: int) -> None:
        """Record *tokens* as consumed."""
        self.used += tokens

    def check(self, output_tokens_this_turn: int) -> TokenBudgetResult:
        """Check if we should continue, nudge, or stop.

        Args:
            output_tokens_this_turn: Number of tokens the model produced
                in the most recent turn.

        Returns:
            A :class:`TokenBudgetResult` indicating the decision.
        """
        pct = self.used / self.budget if self.budget > 0 else 1.0

        if pct >= COMPLETION_THRESHOLD:
            return TokenBudgetResult(should_continue=False, reason="budget_exhausted")

        if output_tokens_this_turn < DIMINISHING_THRESHOLD:
            self._continuation_count += 1
            if self._continuation_count >= 3:
                return TokenBudgetResult(should_continue=False, reason="diminishing_returns")
        else:
            self._continuation_count = 0

        remaining_pct = 1.0 - pct
        if remaining_pct < 0.2:
            return TokenBudgetResult(
                should_continue=True,
                reason="budget_low",
                nudge_message=(
                    f"Token budget is {remaining_pct:.0%} remaining. "
                    "Please wrap up concisely."
                ),
            )

        return TokenBudgetResult(should_continue=True, reason="ok")
