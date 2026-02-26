# chimera/training/strategies/majority_voting.py
from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from chimera.eval.benchmarks.aimo import extract_answer
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


class MajorityVoting(Strategy):
    """Sample N solutions and pick the consensus answer."""

    def __init__(
        self,
        n_samples: int = 16,
        temperature: float = 0.7,
        min_agreement: int = 2,
    ) -> None:
        self.n_samples = n_samples
        self.temperature = temperature
        self.min_agreement = min_agreement

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

            answer = extract_answer(agent_result.output)
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

            # Early stopping: only when enough samples remain that the
            # check is meaningful (remaining >= min_agreement) and the
            # leader cannot be overtaken.
            remaining = self.n_samples - sample_num
            if votes and remaining >= self.min_agreement:
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
