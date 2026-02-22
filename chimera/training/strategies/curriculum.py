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
    from chimera.training.architecture import Architecture, Layer
    from chimera.training.constraint import Constraint
    from chimera.training.spec import Spec


class CurriculumStrategy(Strategy):
    """Synthesize layers in dependency order (curriculum learning).

    Uses the Architecture's ``build_order()`` to process layers from
    foundational to dependent.  Each layer gets its own mini-synthesis:
    build a prompt for that layer, run the agent, run tests.  Frozen
    layers are skipped.
    """

    def __init__(self, architecture: Architecture) -> None:
        self.architecture = architecture

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

        ordered_layers = self.architecture.build_order()
        history: list[EpochResult] = []
        total_cost = 0.0
        best_pass_rate = 0.0
        epoch_num = 0

        for layer in ordered_layers:
            if layer.frozen:
                continue

            epoch_num += 1

            # Build a layer-specific prompt
            task = self._layer_prompt(layer, spec)

            # Run agent for this layer
            agent_result = agent.run(task, env)
            total_cost += agent_result.cost

            # Run tests
            test_result = env.run_tests()

            improved = test_result.pass_rate > best_pass_rate
            if improved:
                best_pass_rate = test_result.pass_rate

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

        converged = best_pass_rate == 1.0 if history else False

        result = SynthesisResult(
            converged=converged,
            iterations=epoch_num,
            total_cost=total_cost,
            best_pass_rate=best_pass_rate,
            history=history,
        )

        for cb in callbacks:
            cb.on_synthesis_end(result)
        return result

    @staticmethod
    def _layer_prompt(layer: Layer, spec: Spec) -> str:
        """Build a synthesis prompt for a single layer."""
        parts = [spec.to_prompt()]
        parts.append(f"\nFocus on layer: {layer.name}")
        if layer.description:
            parts.append(f"Description: {layer.description}")
        if layer.depends_on:
            parts.append(f"Dependencies (already implemented): {', '.join(layer.depends_on)}")
        if layer.template:
            parts.append(f"Template:\n{layer.template}")
        if layer.constraints:
            parts.append(f"Constraints: {', '.join(layer.constraints)}")
        return "\n".join(parts)
