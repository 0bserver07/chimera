# chimera/training/strategies/majority_voting.py
from __future__ import annotations

from collections import Counter
from typing import Callable, TYPE_CHECKING

from chimera.training.strategies.base import (
    Callback,
    EpochResult,
    Strategy,
    SynthesisResult,
)

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.training.constraint import Constraint
    from chimera.training.spec import Spec


def _default_extract_answer(text: str) -> int | None:
    """Default: delegate to the AIMO extractor for backward compatibility."""
    from chimera.eval.benchmarks.aimo import extract_answer
    return extract_answer(text)


class MajorityVoting(Strategy):
    """Sample N solutions and pick the consensus answer.

    Args:
        n_samples: Number of solution attempts per problem.
        temperature: Recommended sampling temperature. Configure this on the
            provider level (e.g. ``provider.temperature = 0.7``) before passing
            the agent to this strategy.
        min_agreement: Minimum votes for a consensus answer.
        extract_fn: Callable that extracts an integer answer from agent output.
            Defaults to the AIMO ``extract_answer`` (looks for ANSWER: N,
            \\boxed{N}, or last integer in text).
    """

    def __init__(
        self,
        n_samples: int = 16,
        temperature: float = 0.7,
        min_agreement: int = 2,
        extract_fn: Callable[[str], int | None] | None = None,
    ) -> None:
        self.n_samples = n_samples
        self.temperature = temperature
        self.min_agreement = min_agreement
        self._extract_fn = extract_fn or _default_extract_answer

    def run(
        self,
        agent: Agent,
        spec: Spec,
        env: Environment,
        constraints: list[Constraint] | None = None,
        callbacks: list[Callback] | None = None,
    ) -> SynthesisResult:
        callbacks = callbacks or []
        for cb in callbacks:
            cb.on_synthesis_start()

        task = spec.to_prompt()
        history: list[EpochResult] = []
        votes: Counter[int] = Counter()
        total_cost = 0.0

        for sample_num in range(1, self.n_samples + 1):
            for cb in callbacks:
                cb.on_epoch_start(sample_num)

            agent_result = agent.run(task, env)
            total_cost += agent_result.cost

            answer = self._extract_fn(agent_result.output)
            if answer is not None:
                votes[answer] += 1

            epoch = EpochResult(
                epoch=sample_num,
                pass_rate=1.0 if answer is not None else 0.0,
                passed=1 if answer is not None else 0,
                total=1,
                agent_output=agent_result.output,
                improved=answer is not None,
                cost=agent_result.cost,
            )
            history.append(epoch)

            should_continue = True
            for cb in callbacks:
                ret = cb.on_epoch_end(sample_num, epoch)
                if ret is False:
                    should_continue = False

            if not should_continue:
                break

            # Early stopping: if the leader has enough agreement and
            # cannot be overtaken by remaining samples, stop early.
            remaining = self.n_samples - sample_num
            if votes:
                top_answer, top_count = votes.most_common(1)[0]
                if top_count >= self.min_agreement:
                    second_count = votes.most_common(2)[-1][1] if len(votes) > 1 else 0
                    if top_count > second_count + remaining:
                        break

        converged = False
        winning_answer = None
        if votes:
            top_answer, top_count = votes.most_common(1)[0]
            if top_count >= self.min_agreement:
                converged = True
                winning_answer = top_answer

        if winning_answer is not None:
            history.append(EpochResult(
                epoch=len(history) + 1,
                pass_rate=1.0,
                passed=1,
                total=1,
                agent_output=f"ANSWER: {winning_answer}",
                improved=True,
                cost=0.0,
            ))

        result = SynthesisResult(
            converged=converged,
            iterations=len(history) - (1 if winning_answer is not None else 0),
            total_cost=total_cost,
            best_pass_rate=1.0 if converged else 0.0,
            history=history,
            failure_reason=None if converged else "No consensus reached",
        )
        for cb in callbacks:
            cb.on_synthesis_end(result)
        return result
