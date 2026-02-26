# chimera/training/strategies/aimo_ensemble.py
from __future__ import annotations

from typing import TYPE_CHECKING

from chimera.training.strategies.base import (
    Callback,
    Strategy,
    SynthesisResult,
)
from chimera.training.strategies.majority_voting import MajorityVoting
from chimera.training.strategies.tree_search import TreeSearch

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.training.constraint import Constraint
    from chimera.training.spec import Spec


class AIMOEnsemble(Strategy):
    """Two-phase strategy: MajorityVoting first, TreeSearch fallback."""

    def __init__(
        self,
        voting_samples: int = 8,
        min_agreement: int = 2,
        temperature: float = 0.7,
        tree_branch_factor: int = 3,
        tree_max_depth: int = 5,
        tree_max_nodes: int = 10,
    ) -> None:
        self.voting_samples = voting_samples
        self.min_agreement = min_agreement
        self.temperature = temperature
        self.tree_branch_factor = tree_branch_factor
        self.tree_max_depth = tree_max_depth
        self.tree_max_nodes = tree_max_nodes

    def run(
        self,
        agent: Agent,
        spec: Spec,
        env: Environment,
        constraints: list[Constraint] | None = None,
        callbacks: list[Callback] | None = None,
    ) -> SynthesisResult:
        # Phase 1: MajorityVoting
        voting = MajorityVoting(
            n_samples=self.voting_samples,
            temperature=self.temperature,
            min_agreement=self.min_agreement,
        )
        result = voting.run(agent, spec, env, constraints, callbacks)
        if result.converged:
            return result

        # Phase 2: TreeSearch fallback
        tree = TreeSearch(
            branch_factor=self.tree_branch_factor,
            max_depth=self.tree_max_depth,
            max_nodes=self.tree_max_nodes,
        )
        tree_result = tree.run(agent, spec, env, constraints, callbacks)

        return SynthesisResult(
            converged=tree_result.converged,
            iterations=result.iterations + tree_result.iterations,
            total_cost=result.total_cost + tree_result.total_cost,
            best_pass_rate=tree_result.best_pass_rate,
            history=result.history + tree_result.history,
            failure_reason=tree_result.failure_reason if not tree_result.converged else None,
        )
