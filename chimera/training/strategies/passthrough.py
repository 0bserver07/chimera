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


class Passthrough(Strategy):
    """Single-shot strategy: run agent once, no iteration."""

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
        agent_result = agent.run(task, env)
        test_result = env.run_tests()

        epoch = EpochResult(
            epoch=1,
            pass_rate=test_result.pass_rate,
            passed=test_result.passed,
            total=test_result.total,
            agent_output=agent_result.output,
            improved=True,
            cost=agent_result.cost,
        )

        result = SynthesisResult(
            converged=test_result.all_passed,
            iterations=1,
            total_cost=agent_result.cost,
            best_pass_rate=test_result.pass_rate,
            history=[epoch],
        )

        for cb in callbacks:
            cb.on_synthesis_end(result)
        return result
