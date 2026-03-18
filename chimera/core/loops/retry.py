"""Retry loop: wrap any inner loop with retry + scoring.

Inspired by SWE-Agent's retry mechanism (``AbstractRetryLoop`` /
``ScoreRetryLoop``), this module provides a lightweight wrapper that
runs an inner loop multiple times and selects the best attempt by
score.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

from chimera.core.context import Context
from chimera.core.loop import ReAct, drain_steps
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.providers.base import Provider
from chimera.types import AgentResult, Message, StepResult

if TYPE_CHECKING:
    from chimera.core.loop_config import LoopConfig


@dataclass
class RetryAttempt:
    """Record of a single retry attempt."""

    attempt: int
    result: AgentResult
    score: float


class RetryLoop:
    """Wrap any inner loop with retry + scoring.

    Runs the inner loop up to ``max_retries`` times.  After each
    attempt the result is scored.  If the score meets or exceeds
    ``success_threshold`` the loop stops early.  Otherwise a new
    attempt is started with a fresh context that includes feedback
    from the previous failure.

    The best attempt (by score) is returned.

    Args:
        inner: The loop to wrap.  Defaults to a :class:`ReAct` instance.
        max_retries: Maximum number of attempts.
        scorer: Callable that maps an :class:`AgentResult` to a float
            score.  Defaults to 1.0 for success, 0.0 otherwise.
        success_threshold: Score at or above which retrying stops.
        config: Optional :class:`LoopConfig` forwarded to the inner loop
            when it is constructed by default.

    Attributes:
        attempts: List of :class:`RetryAttempt` records populated
            during :meth:`run`.
    """

    def __init__(
        self,
        inner: ReAct | Any | None = None,
        max_retries: int = 3,
        scorer: Callable[[AgentResult], float] | None = None,
        success_threshold: float = 1.0,
        config: LoopConfig | None = None,
    ) -> None:
        self._inner = inner or ReAct()
        self._max_retries = max_retries
        self._scorer = scorer or self._default_scorer
        self._success_threshold = success_threshold
        self.config = config
        self.attempts: list[RetryAttempt] = []

    @staticmethod
    def _default_scorer(result: AgentResult) -> float:
        """Default scorer: 1.0 if success, 0.0 otherwise."""
        return 1.0 if result.success else 0.0

    def _build_retry_context(self, original_context: Context, prev: RetryAttempt) -> Context:
        """Build a fresh context for a retry attempt.

        Preserves the system prompt and the original user message, then
        appends a user message describing the previous failure so the
        inner loop can try a different approach.

        Args:
            original_context: The context from the very first attempt.
            prev: The most recent (failed) attempt.

        Returns:
            A new :class:`Context` ready for the next attempt.
        """
        ctx = Context(system=original_context.system)
        # Keep the original user message(s)
        for msg in original_context.messages:
            if msg.role == "user":
                ctx.add(msg)
                break
        # Append retry feedback
        output_preview = prev.result.output[:500] if prev.result.output else "(no output)"
        ctx.add(Message.user(
            f"Previous attempt (#{prev.attempt}) scored {prev.score:.0%}. "
            f"Output was: {output_preview}\n\n"
            f"Try a different approach."
        ))
        return ctx

    def run(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> AgentResult:
        """Run the inner loop with retries, returning the best attempt.

        Args:
            provider: LLM provider.
            tools: Available tools.
            context: Initial conversation context.
            env: Execution environment (optional).

        Returns:
            The :class:`AgentResult` from the highest-scoring attempt.
        """
        self.attempts.clear()
        best: RetryAttempt | None = None

        for attempt_num in range(1, self._max_retries + 1):
            # Build context for this attempt
            if attempt_num == 1:
                ctx = context
            else:
                ctx = self._build_retry_context(context, self.attempts[-1])

            # Run inner loop
            result = self._inner.run(provider, tools, ctx, env)
            score = self._scorer(result)

            attempt = RetryAttempt(
                attempt=attempt_num, result=result, score=score,
            )
            self.attempts.append(attempt)

            if best is None or score > best.score:
                best = attempt

            # Success threshold met — stop retrying
            if score >= self._success_threshold:
                break

        if best is not None:
            return best.result

        return AgentResult(
            output="",
            steps=0,
            tool_calls_total=0,
            cost=0.0,
            success=False,
            error="No attempts completed",
        )

    def iter_steps(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> Generator[StepResult, None, AgentResult]:
        """Delegate to the inner loop's ``iter_steps``.

        Retry semantics apply only to :meth:`run`.  When streaming
        steps via ``iter_steps`` the caller gets the raw inner-loop
        behaviour without automatic retries.
        """
        return self._inner.iter_steps(provider, tools, context, env)
