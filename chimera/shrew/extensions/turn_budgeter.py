"""Soft per-turn token budget with proximity warnings.

Small models that overshoot their context window degrade ungracefully:
the runtime evicts old turns, the agent forgets the user's goal, and
loop budgets blow up. This module ships a pure helper that estimates
tokens used for the current turn and reports whether the agent should
be warned, soft-capped, or stopped.

Public surface:

* :func:`estimate_tokens` — character-count-based heuristic (4
  chars/token, the usual rule of thumb).
* :func:`check_budget` — returns a :data:`BudgetStatus` literal:
  ``"ok"``, ``"warn"``, or ``"exceeded"``.
* :func:`format_budget_warning` — short message suitable for the
  agent's observation channel.

Stdlib-only. No global state. No actual tokenizer (callers can swap
in a real one by passing pre-computed counts).
"""
from __future__ import annotations

from typing import Final, Literal

__all__ = [
    "BudgetStatus",
    "DEFAULT_TURN_BUDGET",
    "WARN_FRACTION",
    "check_budget",
    "estimate_tokens",
    "format_budget_warning",
]


#: Default per-turn token soft cap. 4096 tokens covers a substantive
#: turn (a tool call, its output, the agent's response) without
#: encouraging the model to dump entire files into observations.
DEFAULT_TURN_BUDGET: Final[int] = 4096

#: Threshold above which we emit a warning (but don't yet stop).
#: 0.8 = 80% of budget.
WARN_FRACTION: Final[float] = 0.8


BudgetStatus = Literal["ok", "warn", "exceeded"]


def estimate_tokens(text: str) -> int:
    """Estimate the token count of ``text`` using a 4-char heuristic.

    The 4-chars-per-token rule of thumb is what ``tiktoken`` and the
    Anthropic SDK recommend for fast, tokenizer-free estimates.
    Code-heavy text comes in lower (3 chars/token); prose comes in
    higher (5 chars/token). Either way, this is a budgeting helper,
    not an oracle.
    """
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def check_budget(
    used_tokens: int,
    budget: int = DEFAULT_TURN_BUDGET,
    *,
    warn_fraction: float = WARN_FRACTION,
) -> BudgetStatus:
    """Classify ``used_tokens`` against ``budget``.

    Args:
        used_tokens: Tokens spent on the current turn so far.
        budget: Soft cap. Values <= 0 always return ``"ok"`` (the
            budget is effectively disabled).
        warn_fraction: Fraction of ``budget`` at which we cross from
            ``"ok"`` to ``"warn"``. Values outside ``[0, 1]`` are
            clamped.

    Returns:
        ``"ok"`` when within ``warn_fraction * budget``;
        ``"warn"`` when between that and ``budget``;
        ``"exceeded"`` when at or above ``budget``.
    """
    if budget <= 0 or used_tokens <= 0:
        return "ok"
    fraction = min(max(warn_fraction, 0.0), 1.0)
    threshold = int(budget * fraction)
    if used_tokens >= budget:
        return "exceeded"
    if used_tokens >= threshold:
        return "warn"
    return "ok"


def format_budget_warning(
    used_tokens: int,
    budget: int = DEFAULT_TURN_BUDGET,
) -> str:
    """Return a short human-readable warning for the observation channel."""
    status = check_budget(used_tokens, budget)
    if status == "ok":
        return ""
    pct = (used_tokens * 100 // max(budget, 1))
    if status == "exceeded":
        return (
            f"[turn-budget exceeded: {used_tokens}/{budget} tokens "
            f"({pct}%) — wrap up the current step]"
        )
    return (
        f"[turn-budget warning: {used_tokens}/{budget} tokens "
        f"({pct}%) — be concise]"
    )
