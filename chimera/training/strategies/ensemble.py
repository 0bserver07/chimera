from __future__ import annotations

from typing import TYPE_CHECKING

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


class EnsembleStrategy(Strategy):
    """Run multiple synthesis attempts, pick the best result.

    Runs the same agent (or different agents) multiple times, each time
    from a fresh checkpoint, and selects the attempt with the highest
    pass rate.  This is a *training strategy* -- different from
    ``chimera.composition.Ensemble`` which composes agent outputs.
    """

    def __init__(self, attempts: int = 3) -> None:
        self.attempts = attempts

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
        best_epoch: EpochResult | None = None
        best_checkpoint: str | None = None
        history: list[EpochResult] = []
        total_cost = 0.0

        # Save baseline so we can restore between attempts
        baseline = env.checkpoint()

        for attempt in range(1, self.attempts + 1):
            # Restore to baseline before each attempt
            env.restore(baseline)

            agent_result = agent.run(task, env)
            total_cost += agent_result.cost

            test_result = env.run_tests()

            improved = (
                best_epoch is None or test_result.pass_rate > best_epoch.pass_rate
            )

            epoch = EpochResult(
                epoch=attempt,
                pass_rate=test_result.pass_rate,
                passed=test_result.passed,
                total=test_result.total,
                agent_output=agent_result.output,
                improved=improved,
                cost=agent_result.cost,
            )
            history.append(epoch)

            for cb in callbacks:
                cb.on_epoch_end(epoch)

            if improved:
                best_epoch = epoch
                best_checkpoint = env.checkpoint()

        # Restore best attempt
        if best_checkpoint is not None:
            env.restore(best_checkpoint)

        best_rate = best_epoch.pass_rate if best_epoch else 0.0
        converged = best_rate == 1.0

        result = SynthesisResult(
            converged=converged,
            iterations=self.attempts,
            total_cost=total_cost,
            best_pass_rate=best_rate,
            history=history,
        )

        for cb in callbacks:
            cb.on_synthesis_end(result)
        return result
