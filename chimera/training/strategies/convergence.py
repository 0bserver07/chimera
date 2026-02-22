"""TestConvergence — the default synthesis strategy.

Iterate until all tests pass, with checkpointing and rollback.

Each epoch:
  1. Build a prompt from spec + test failures
  2. Run agent
  3. Run tests
  4. If improved -> checkpoint
  5. If regressed -> rollback to best
  6. If converged -> return success
  7. If patience exhausted -> return best result
"""

from __future__ import annotations

from typing import Any

from chimera.training.strategies.base import (
    Callback,
    EpochResult,
    Strategy,
    SynthesisResult,
)
from chimera.training.constraint import evaluate_all, all_satisfied


class TestConvergence(Strategy):
    """Converge by running agent until tests pass or patience exhausted."""

    def __init__(self, max_iterations: int = 50, patience: int = 5) -> None:
        self.max_iterations = max_iterations
        self.patience = patience

    def run(
        self,
        agent: Any,
        spec: Any,
        env: Any,
        constraints: list[Any] | None = None,
        callbacks: list[Callback] | None = None,
    ) -> SynthesisResult:
        """Execute the synthesis strategy."""
        callbacks = callbacks or []
        constraints = constraints or []

        # Notify callbacks
        for cb in callbacks:
            cb.on_synthesis_start()

        best_pass_rate = 0.0
        best_checkpoint: str | None = None
        epochs_without_improvement = 0
        history: list[EpochResult] = []
        total_cost = 0.0

        for epoch in range(1, self.max_iterations + 1):
            # Notify callbacks
            for cb in callbacks:
                cb.on_epoch_start(epoch)

            # Build prompt: spec + previous test failures
            task = self._build_task(spec, history)

            # Run agent
            agent_result = agent.run(task, env)
            total_cost += agent_result.cost

            # Run tests
            test_result = env.run_tests()

            # Create epoch result
            improved = test_result.pass_rate > best_pass_rate
            checkpoint_id = None

            if improved:
                best_pass_rate = test_result.pass_rate
                checkpoint_id = env.checkpoint()
                best_checkpoint = checkpoint_id
                epochs_without_improvement = 0
            else:
                epochs_without_improvement += 1
                # Rollback to best if we regressed
                if best_checkpoint and test_result.pass_rate < best_pass_rate:
                    env.restore(best_checkpoint)

            epoch_result = EpochResult(
                epoch=epoch,
                pass_rate=test_result.pass_rate,
                passed=test_result.passed,
                total=test_result.total,
                agent_output=agent_result.output,
                checkpoint_id=checkpoint_id,
                improved=improved,
                cost=agent_result.cost,
            )
            history.append(epoch_result)

            # Notify callbacks -- stop if any returns False
            should_continue = True
            for cb in callbacks:
                if cb.on_epoch_end(epoch, epoch_result) is False:
                    should_continue = False

            # Check convergence
            if test_result.all_passed:
                # Also check constraints if any
                if constraints:
                    constraint_results = evaluate_all(constraints, env)
                    if not all_satisfied(constraint_results):
                        # Tests pass but constraints don't -- keep going
                        if not should_continue:
                            break
                        if epochs_without_improvement >= self.patience:
                            break
                        continue

                result = SynthesisResult(
                    converged=True,
                    iterations=epoch,
                    total_cost=total_cost,
                    best_pass_rate=best_pass_rate,
                    history=history,
                )
                for cb in callbacks:
                    cb.on_synthesis_end(result)
                return result

            if not should_continue:
                break

            # Check patience
            if epochs_without_improvement >= self.patience:
                break

        # Did not converge
        result = SynthesisResult(
            converged=False,
            iterations=len(history),
            total_cost=total_cost,
            best_pass_rate=best_pass_rate,
            history=history,
            failure_reason=(
                f"Did not converge after {len(history)} iterations "
                f"(best: {best_pass_rate:.1%})"
            ),
        )
        for cb in callbacks:
            cb.on_synthesis_end(result)
        return result

    def _build_task(self, spec: Any, history: list[EpochResult]) -> str:
        """Build the task prompt for the agent."""
        parts = [spec.to_prompt()]

        if history:
            last = history[-1]
            parts.append(
                f"\n\nPrevious attempt: {last.passed}/{last.total} tests passed."
            )
            if last.total > 0 and not (last.passed == last.total):
                parts.append("Fix the failing tests and try again.")

        return "\n".join(parts)
