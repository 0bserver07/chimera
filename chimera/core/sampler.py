"""Action Sampler: parallel completion sampling with scoring.

Ported from SWE-Agent's approach. Generates N completions in parallel,
scores each using a configurable scorer, and returns the best one.

Example::

    sampler = ActionSampler(provider, n=3)
    best = sampler.sample(messages, tools, scorer=length_scorer)
"""
from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Protocol

if TYPE_CHECKING:
    from chimera.providers.base import Provider, Response
    from chimera.types import Message


class Scorer(Protocol):
    """Protocol for scoring a completion response."""

    def __call__(self, response: "Response") -> float: ...


def default_scorer(response: "Response") -> float:
    """Score by content length (longer = more detailed = better)."""
    return float(len(response.content))


def tool_call_scorer(response: "Response") -> float:
    """Prefer responses that include tool calls (action-oriented)."""
    base = float(len(response.content))
    if response.has_tool_calls:
        base += 1000.0
    return base


@dataclass
class SampledResult:
    """Result of a sampling round.

    Args:
        best: The highest-scoring response.
        all_responses: All N responses generated.
        scores: Parallel list of scores.
        best_index: Index of the winning response.
    """

    best: "Response"
    all_responses: list["Response"] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    best_index: int = 0


ToolSchema = dict[str, Any]


class ActionSampler:
    """Generate N completions in parallel and pick the best.

    Args:
        provider: LLM provider to use.
        n: Number of parallel samples to generate.
        temperature: Sampling temperature (higher = more diverse).
        max_workers: Max parallel threads for sampling.
    """

    def __init__(
        self,
        provider: "Provider",
        n: int = 3,
        temperature: float = 0.8,
        max_workers: int | None = None,
    ) -> None:
        self._provider = provider
        self._n = n
        self._temperature = temperature
        self._max_workers = max_workers or n

    def sample(
        self,
        messages: list["Message"],
        tools: list[ToolSchema] | None = None,
        scorer: Scorer | None = None,
    ) -> SampledResult:
        """Generate N completions and return the best.

        Args:
            messages: Conversation context to send to the provider.
            tools: Optional tool schemas.
            scorer: Scoring function. Defaults to content-length scorer.

        Returns:
            A SampledResult with the best response and all candidates.
        """
        score_fn = scorer or default_scorer

        def _call(_i: int) -> "Response":
            return self._provider.complete(
                messages,
                tools=tools,
                temperature=self._temperature,
            )

        # Parallel sampling
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self._max_workers,
        ) as pool:
            futures = [pool.submit(_call, i) for i in range(self._n)]
            responses = [f.result() for f in futures]

        scores = [score_fn(r) for r in responses]
        best_idx = max(range(len(scores)), key=lambda i: scores[i])

        return SampledResult(
            best=responses[best_idx],
            all_responses=responses,
            scores=scores,
            best_index=best_idx,
        )

    def sample_sequential(
        self,
        messages: list["Message"],
        tools: list[ToolSchema] | None = None,
        scorer: Scorer | None = None,
    ) -> SampledResult:
        """Like :meth:`sample` but generates completions sequentially.

        Useful when the provider does not support concurrent requests.
        """
        score_fn = scorer or default_scorer
        responses = []
        for _ in range(self._n):
            resp = self._provider.complete(
                messages,
                tools=tools,
                temperature=self._temperature,
            )
            responses.append(resp)

        scores = [score_fn(r) for r in responses]
        best_idx = max(range(len(scores)), key=lambda i: scores[i])

        return SampledResult(
            best=responses[best_idx],
            all_responses=responses,
            scores=scores,
            best_index=best_idx,
        )
