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


class TestConvergence(Strategy):
    """Iterate until all tests pass or patience is exhausted.

    Each epoch: agent generates/modifies code -> run tests -> measure pass
    rate -> if not converged, agent sees failures and tries again.
    Rolls back on regression.
    """

    def __init__(
        self,
        max_iterations: int = 100,
        patience: int = 5,
    ) -> None:
        self.max_iterations = max_iterations
        self.patience = patience

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

        history: list[EpochResult] = []
        best_pass_rate = 0.0
        best_checkpoint: str | None = None
        stale_epochs = 0
        total_cost = 0.0

        for epoch_num in range(1, self.max_iterations + 1):
            # Build task prompt -- include test failures if we have history
            task = spec.to_prompt()
            if history:
                last = history[-1]
                task += f"\n\nPrevious pass rate: {last.pass_rate:.0%}. Fix remaining failures."

            # Run agent
            agent_result = agent.run(task, env)
            total_cost += agent_result.cost

            # Run tests
            test_result = env.run_tests()

            improved = test_result.pass_rate > best_pass_rate

            epoch = EpochResult(
                epoch=epoch_num,
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
                best_pass_rate = test_result.pass_rate
                best_checkpoint = env.checkpoint()
                stale_epochs = 0
            else:
                stale_epochs += 1
                # Rollback on regression
                if best_checkpoint is not None and test_result.pass_rate < best_pass_rate:
                    env.restore(best_checkpoint)

            # Converged?
            if test_result.all_passed:
                result = SynthesisResult(
                    converged=True,
                    iterations=epoch_num,
                    total_cost=total_cost,
                    best_pass_rate=best_pass_rate,
                    history=history,
                )
                for cb in callbacks:
                    cb.on_synthesis_end(result)
                return result

            # Patience exhausted?
            if stale_epochs >= self.patience:
                result = SynthesisResult(
                    converged=False,
                    iterations=epoch_num,
                    total_cost=total_cost,
                    best_pass_rate=best_pass_rate,
                    history=history,
                    failure_reason=f"No improvement for {self.patience} epochs",
                )
                for cb in callbacks:
                    cb.on_synthesis_end(result)
                return result

        result = SynthesisResult(
            converged=False,
            iterations=self.max_iterations,
            total_cost=total_cost,
            best_pass_rate=best_pass_rate,
            history=history,
            failure_reason="Max iterations reached",
        )
        for cb in callbacks:
            cb.on_synthesis_end(result)
        return result
