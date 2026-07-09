"""Uniform run budgets for controlled comparative evaluation.

The comparative matrix ("same model, same budget, different agent
architectures") is only defensible if the budget unit is identical for
every agent regardless of its loop's natural notion of a "step" (ReAct
counts thought-action pairs, Plan-and-Execute counts plan steps,
Reflexion counts attempt iterations). The universal unit here is the
**tool call**, because every loop routes tool execution through
:mod:`chimera.core.tool_executor`; LLM-call, wall-clock, and dollar caps
act as orthogonal guards.

Enforcement is cooperative and rides the existing cancellation
machinery: each completed unit is recorded against a
:class:`BudgetEnforcer`, and the first time the
:class:`BudgetSpec` is exhausted the enforcer cancels the run's
:class:`~chimera.core.cancellation.CancellationToken`. Loops then stop
exactly the way they stop for a user cancel — no per-loop budget wiring
required. The "after-completion" semantics deliberately allow the unit
that tips the budget to finish (the Nth tool call runs; the (N+1)th
never starts).

Reporting stays distinct from failure: the enforcer's
:attr:`~BudgetEnforcer.exhausted_reason` records *which* cap tripped, so
report layers can show budget hits in their own column rather than
conflating them with task failures.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.core.cancellation import CancellationToken
    from chimera.providers.base import Provider

__all__ = ["BudgetSpec", "BudgetTally", "BudgetEnforcer", "BudgetedProvider"]


@dataclass(frozen=True)
class BudgetSpec:
    """Caps for one task run. ``None`` fields are unlimited.

    Attributes:
        max_tool_calls: Primary normalized unit — completed tool calls.
        max_llm_calls: Completed provider ``complete()`` calls.
        max_wall_clock_sec: Elapsed seconds since :meth:`BudgetEnforcer.start`.
        max_cost_usd: Accumulated provider cost in dollars.
    """

    max_tool_calls: int | None = None
    max_llm_calls: int | None = None
    max_wall_clock_sec: float | None = None
    max_cost_usd: float | None = None

    def is_exhausted(self, tally: BudgetTally) -> tuple[bool, str | None]:
        """Judge a tally against this spec.

        Args:
            tally: Counters recorded so far.

        Returns:
            ``(True, reason)`` naming the first exhausted cap, else
            ``(False, None)``.
        """
        if self.max_tool_calls is not None and tally.tool_calls >= self.max_tool_calls:
            return True, f"tool_calls ({tally.tool_calls}/{self.max_tool_calls})"
        if self.max_llm_calls is not None and tally.llm_calls >= self.max_llm_calls:
            return True, f"llm_calls ({tally.llm_calls}/{self.max_llm_calls})"
        if (
            self.max_wall_clock_sec is not None
            and tally.elapsed_sec >= self.max_wall_clock_sec
        ):
            return True, f"wall_clock ({tally.elapsed_sec:.1f}s/{self.max_wall_clock_sec:.0f}s)"
        if self.max_cost_usd is not None and tally.cost_usd >= self.max_cost_usd:
            return True, f"cost (${tally.cost_usd:.4f}/${self.max_cost_usd:.2f})"
        return False, None


@dataclass
class BudgetTally:
    """Mutable counters for one task run."""

    tool_calls: int = 0
    llm_calls: int = 0
    cost_usd: float = 0.0
    started_at: float | None = None

    @property
    def elapsed_sec(self) -> float:
        """Seconds since :attr:`started_at`; ``0.0`` before start."""
        if self.started_at is None:
            return 0.0
        return time.monotonic() - self.started_at


class BudgetEnforcer:
    """Thread-safe budget bookkeeping that trips a cancellation token.

    Record each unit *after* it completes; the first record (or
    :meth:`check`) that exhausts the spec stores
    :attr:`exhausted_reason` and cancels the token, after which the
    loop's own cooperative-cancel path stops the run.

    Args:
        spec: The caps to enforce.
        cancellation: Token shared with the run's
            :class:`~chimera.core.loop_config.LoopConfig`. Optional —
            without it the enforcer only tallies and reports.
    """

    def __init__(
        self,
        spec: BudgetSpec,
        cancellation: "CancellationToken | None" = None,
    ) -> None:
        self.spec = spec
        self.tally = BudgetTally()
        self._cancellation = cancellation
        self._lock = threading.Lock()
        self.exhausted_reason: str | None = None

    @property
    def exhausted(self) -> bool:
        """Whether any cap has tripped."""
        return self.exhausted_reason is not None

    def start(self) -> None:
        """Start the wall clock (idempotent)."""
        with self._lock:
            if self.tally.started_at is None:
                self.tally.started_at = time.monotonic()

    def record_tool_call(self, tool_name: str = "") -> None:
        """Record one completed tool call and trip the budget if exhausted.

        Args:
            tool_name: Informational; not used for accounting.
        """
        with self._lock:
            self.tally.tool_calls += 1
            self._trip_if_exhausted()

    def record_llm_call(self, cost: float = 0.0) -> None:
        """Record one completed provider call (and its cost).

        Args:
            cost: Dollar cost of the call, when known.
        """
        with self._lock:
            self.tally.llm_calls += 1
            self.tally.cost_usd += cost
            self._trip_if_exhausted()

    def check(self) -> None:
        """Re-evaluate caps (the wall clock advances without records)."""
        with self._lock:
            self._trip_if_exhausted()

    def _trip_if_exhausted(self) -> None:
        # Caller holds the lock.
        if self.exhausted_reason is not None:
            return
        hit, reason = self.spec.is_exhausted(self.tally)
        if hit:
            self.exhausted_reason = reason
            if self._cancellation is not None:
                self._cancellation.cancel()


class BudgetedProvider:
    """Provider wrapper that records LLM calls and cost against an enforcer.

    Delegates everything to the wrapped provider; ``complete()`` is
    recorded after it returns (the call that tips the budget is allowed
    to finish). Cost is computed from the response ``usage`` via
    :func:`chimera.providers.cost.calculate_cost` when available.

    Args:
        inner: The real provider.
        enforcer: Shared per-run enforcer.
    """

    def __init__(self, inner: "Provider", enforcer: BudgetEnforcer) -> None:
        self._inner = inner
        self._enforcer = enforcer

    def _cost_of(self, usage: Any) -> float:
        """Price a usage dict, defaulting to 0.0 on any failure."""
        if not usage:
            return 0.0
        try:
            from chimera.providers.cost import calculate_cost

            return calculate_cost(self.model_name, usage)
        except Exception:
            return 0.0

    def complete(self, messages: Any, **kwargs: Any) -> Any:
        response = self._inner.complete(messages, **kwargs)
        self._enforcer.record_llm_call(cost=self._cost_of(getattr(response, "usage", None)))
        return response

    async def async_complete(self, messages: Any, **kwargs: Any) -> Any:
        """Async non-streaming call — record cost so the budget can trip.

        The assembled ``AgentLoop`` (``chimera code`` and its presets) drives
        the provider through the async surface; without this wrapper those
        calls fell through ``__getattr__`` unrecorded, so ``max_cost`` /
        ``max_llm_calls`` never tripped for assembled agents.
        """
        response = await self._inner.async_complete(messages, **kwargs)
        self._enforcer.record_llm_call(cost=self._cost_of(getattr(response, "usage", None)))
        return response

    def stream(self, messages: Any, **kwargs: Any) -> Any:
        recorded = False
        for event in self._inner.stream(messages, **kwargs):
            usage = getattr(event, "usage", None)
            if usage and not recorded:
                self._enforcer.record_llm_call(cost=self._cost_of(usage))
                recorded = True
            yield event
        if not recorded:  # a stream with no usage event still is one call
            self._enforcer.record_llm_call(cost=0.0)

    async def async_stream(self, messages: Any, **kwargs: Any) -> Any:
        """Async streaming call — record cost off the usage-bearing event.

        ``StreamEvent.usage`` is set on the terminal ``done`` event, so cost is
        recorded once, near stream end — the call that tips the budget is
        allowed to finish, then the enforcer trips on the next check.
        """
        recorded = False
        async for event in self._inner.async_stream(messages, **kwargs):
            usage = getattr(event, "usage", None)
            if usage and not recorded:
                self._enforcer.record_llm_call(cost=self._cost_of(usage))
                recorded = True
            yield event
        if not recorded:
            self._enforcer.record_llm_call(cost=0.0)

    @property
    def model_name(self) -> str:
        return getattr(self._inner, "model_name", "")

    @property
    def context_window(self) -> int:
        return getattr(self._inner, "context_window", 0)

    @property
    def supports_tool_use(self) -> bool:
        return getattr(self._inner, "supports_tool_use", True)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)
