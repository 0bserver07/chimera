"""Cumulative cost tracking with granular token breakdown."""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Callable


__all__ = [
    "CostLimitExceeded",
    "CostTracker",
    "StepUsage",
    "TokenUsage",
]


class CostLimitExceeded(Exception):
    """Raised when cost budget is exceeded."""


@dataclass
class TokenUsage:
    """Detailed token usage for a single LLM call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    model: str = ""
    timestamp: float = 0.0

    @property
    def cache_hit_rate(self) -> float:
        """Fraction of input tokens served from cache."""
        total_input = self.input_tokens + self.cache_read_tokens
        if total_input == 0:
            return 0.0
        return self.cache_read_tokens / total_input

    @property
    def effective_input_tokens(self) -> int:
        """Input tokens actually processed (not from cache)."""
        return self.input_tokens - self.cache_read_tokens


@dataclass
class StepUsage:
    """Aggregated usage for a single agent step."""

    step_index: int = 0
    calls: list[TokenUsage] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def total_input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def total_output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    @property
    def total_cache_read_tokens(self) -> int:
        return sum(c.cache_read_tokens for c in self.calls)

    @property
    def total_cache_write_tokens(self) -> int:
        return sum(c.cache_write_tokens for c in self.calls)

    @property
    def total_reasoning_tokens(self) -> int:
        return sum(c.reasoning_tokens for c in self.calls)

    @property
    def total_cost(self) -> float:
        return sum(c.cost for c in self.calls)

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


class CostTracker:
    """Track cumulative LLM costs with granular token breakdown.

    Supports per-model breakdowns, per-step tracking, cache/reasoning
    token accounting, context window utilization, and budget enforcement.

    Args:
        budget: Maximum allowed cost in USD. None means unlimited.
        max_context_tokens: Maximum context window size for utilization tracking.
        on_usage_update: Callback fired after each recorded LLM call.
    """

    def __init__(
        self,
        budget: float | None = None,
        max_context_tokens: int | None = None,
        on_usage_update: Callable[[TokenUsage], None] | None = None,
    ) -> None:
        self._budget = budget
        self.max_context_tokens = max_context_tokens
        self.on_usage_update = on_usage_update

        # Cumulative totals
        self._total = 0.0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_cache_read_tokens: int = 0
        self.total_cache_write_tokens: int = 0
        self.total_reasoning_tokens: int = 0
        self.total_cost: float = 0.0
        self.total_calls: int = 0

        # Per-model breakdown
        self._by_model: dict[str, dict] = {}

        # Per-step tracking
        self._steps: list[StepUsage] = []
        self._current_step: StepUsage | None = None

        # Context window tracking
        self._last_context_tokens: int = 0

    def record(self, cost: float, model: str = "") -> None:
        """Record a simple cost (backward-compatible API).

        Args:
            cost: Cost in USD.
            model: Model identifier.

        Raises:
            CostLimitExceeded: If budget is exceeded.
        """
        new_total = self._total + cost
        if self._budget is not None and new_total > self._budget:
            raise CostLimitExceeded(
                f"Cost limit exceeded: ${new_total:.4f} > ${self._budget:.4f}"
            )
        self._total = new_total
        self.total_cost = self._total
        self._by_model.setdefault(model, {
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
            "reasoning_tokens": 0, "cost": 0.0, "calls": 0,
        })
        self._by_model[model]["cost"] += cost
        self._by_model[model]["calls"] += 1
        self.total_calls += 1

    def record_usage(
        self,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        cost: float | None = None,
        context_tokens: int | None = None,
    ) -> TokenUsage:
        """Record a single LLM call's token usage.

        Args:
            model: Model identifier.
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.
            cache_read_tokens: Input tokens served from cache.
            cache_write_tokens: Input tokens written to cache.
            reasoning_tokens: Reasoning/thinking tokens.
            cost: Cost in USD. If None, calculated from pricing table.
            context_tokens: Current context window usage.

        Returns:
            TokenUsage record for this call.

        Raises:
            CostLimitExceeded: If budget is exceeded.
        """
        if cost is None:
            cost = _calculate_granular_cost(
                model, input_tokens, output_tokens,
                cache_read_tokens, reasoning_tokens,
            )

        usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            reasoning_tokens=reasoning_tokens,
            total_tokens=input_tokens + output_tokens,
            cost=cost,
            model=model,
            timestamp=monotonic(),
        )

        # Update cumulative totals
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cache_read_tokens += cache_read_tokens
        self.total_cache_write_tokens += cache_write_tokens
        self.total_reasoning_tokens += reasoning_tokens
        self.total_cost += cost
        self._total += cost
        self.total_calls += 1

        # Update per-model
        if model not in self._by_model:
            self._by_model[model] = {
                "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "cache_write_tokens": 0,
                "reasoning_tokens": 0, "cost": 0.0, "calls": 0,
            }
        m = self._by_model[model]
        m["input_tokens"] += input_tokens
        m["output_tokens"] += output_tokens
        m["cache_read_tokens"] += cache_read_tokens
        m["cache_write_tokens"] += cache_write_tokens
        m["reasoning_tokens"] += reasoning_tokens
        m["cost"] += cost
        m["calls"] += 1

        # Update step tracking
        if self._current_step:
            self._current_step.calls.append(usage)

        # Update context window
        if context_tokens is not None:
            self._last_context_tokens = context_tokens

        # Budget check
        if self._budget is not None and self._total > self._budget:
            raise CostLimitExceeded(
                f"Budget exceeded: ${self._total:.4f} > ${self._budget:.4f}"
            )

        # Callback
        if self.on_usage_update:
            self.on_usage_update(usage)

        return usage

    # -- Step tracking -------------------------------------------------------

    def start_step(self, step_index: int) -> None:
        """Mark the start of an agent step.

        Args:
            step_index: Index of the step being started.
        """
        self._current_step = StepUsage(
            step_index=step_index,
            start_time=monotonic(),
        )

    def end_step(self) -> StepUsage | None:
        """Mark the end of the current agent step.

        Returns:
            The completed StepUsage, or None if no step was active.
        """
        if self._current_step:
            self._current_step.end_time = monotonic()
            self._steps.append(self._current_step)
            step = self._current_step
            self._current_step = None
            return step
        return None

    # -- Queries -------------------------------------------------------------

    @property
    def total(self) -> float:
        """Total cost recorded so far."""
        return self._total

    @property
    def remaining(self) -> float | None:
        """Remaining budget, or None if no budget set."""
        if self._budget is None:
            return None
        return self._budget - self._total

    @property
    def budget_remaining(self) -> float | None:
        """Alias for remaining."""
        return self.remaining

    @property
    def context_utilization(self) -> float:
        """How full the context window is (0.0 to 1.0)."""
        if not self.max_context_tokens:
            return 0.0
        return self._last_context_tokens / self.max_context_tokens

    @property
    def cache_hit_rate(self) -> float:
        """Overall cache hit rate."""
        total = self.total_input_tokens + self.total_cache_read_tokens
        if total == 0:
            return 0.0
        return self.total_cache_read_tokens / total

    @property
    def steps(self) -> list[StepUsage]:
        """List of completed step usages."""
        return list(self._steps)

    @property
    def by_model(self) -> dict[str, dict]:
        """Per-model usage breakdown."""
        return dict(self._by_model)

    def most_expensive_step(self) -> StepUsage | None:
        """Return the step with the highest total cost."""
        if not self._steps:
            return None
        return max(self._steps, key=lambda s: s.total_cost)

    def breakdown(self) -> dict[str, float]:
        """Per-model cost breakdown (backward-compatible)."""
        return {k: v["cost"] for k, v in self._by_model.items()}

    def summary(self) -> dict:
        """Get a full usage summary.

        Returns:
            Dict with all tracked metrics.
        """
        expensive = self.most_expensive_step()
        return {
            "total_cost": self.total_cost,
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cache_read_tokens": self.total_cache_read_tokens,
            "total_cache_write_tokens": self.total_cache_write_tokens,
            "total_reasoning_tokens": self.total_reasoning_tokens,
            "cache_hit_rate": self.cache_hit_rate,
            "context_utilization": self.context_utilization,
            "budget": self._budget,
            "budget_remaining": self.budget_remaining,
            "by_model": self.by_model,
            "steps": len(self._steps),
            "most_expensive_step": expensive.step_index if expensive else None,
        }

    def reset(self) -> None:
        """Reset all tracked costs and usage."""
        self._total = 0.0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read_tokens = 0
        self.total_cache_write_tokens = 0
        self.total_reasoning_tokens = 0
        self.total_cost = 0.0
        self.total_calls = 0
        self._by_model.clear()
        self._steps.clear()
        self._current_step = None
        self._last_context_tokens = 0


# -- Granular cost calculation -----------------------------------------------

MODEL_PRICING: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-opus-4": {
        "input": 15.0, "output": 75.0,
        "cache_read": 1.5, "cache_write": 18.75,
    },
    "claude-sonnet-4": {
        "input": 3.0, "output": 15.0,
        "cache_read": 0.3, "cache_write": 3.75,
    },
    "claude-haiku-4": {
        "input": 0.80, "output": 4.0,
        "cache_read": 0.08, "cache_write": 1.0,
    },
    # OpenAI
    "gpt-4o": {
        "input": 2.50, "output": 10.0,
        "cache_read": 1.25,
    },
    "o1": {
        "input": 15.0, "output": 60.0,
        "reasoning": 60.0, "cache_read": 7.5,
    },
    "o3-mini": {
        "input": 1.10, "output": 4.40,
        "reasoning": 4.40, "cache_read": 0.55,
    },
    # Default fallback
    "default": {
        "input": 3.0, "output": 15.0,
        "cache_read": 0.3,
    },
}


def _calculate_granular_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> float:
    """Calculate cost with cache and reasoning token pricing."""
    # Match longest prefix first
    pricing = None
    for prefix in sorted(MODEL_PRICING, key=len, reverse=True):
        if prefix != "default" and model.startswith(prefix):
            pricing = MODEL_PRICING[prefix]
            break
    if pricing is None:
        pricing = MODEL_PRICING.get("default", {})
    if not pricing:
        return 0.0

    cost = 0.0
    # Regular input tokens (minus cached)
    effective_input = max(0, input_tokens - cache_read_tokens)
    cost += effective_input * pricing["input"] / 1_000_000

    # Cache read tokens (discounted)
    cache_read_price = pricing.get("cache_read", pricing["input"] * 0.1)
    cost += cache_read_tokens * cache_read_price / 1_000_000

    # Output tokens
    cost += output_tokens * pricing["output"] / 1_000_000

    # Reasoning tokens
    reasoning_price = pricing.get("reasoning", pricing["output"])
    cost += reasoning_tokens * reasoning_price / 1_000_000

    return cost
