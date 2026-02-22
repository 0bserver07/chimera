from __future__ import annotations

from typing import TYPE_CHECKING

from chimera.training.strategies.base import Callback, SynthesisResult
from chimera.training.strategies.convergence import TestConvergence

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.training.architecture import Architecture
    from chimera.training.constraint import Constraint
    from chimera.training.spec import Spec
    from chimera.training.strategies.base import Strategy


class Trainer:
    """Ties together Architecture + Spec + Agent + Strategy + Constraints + Environment.

    The main orchestrator for code synthesis.
    """

    def __init__(
        self,
        spec: Spec,
        agent: Agent,
        env: Environment,
        architecture: Architecture | None = None,
        constraints: list[Constraint] | None = None,
    ) -> None:
        self.architecture = architecture
        self.spec = spec
        self.agent = agent
        self.env = env
        self.constraints = constraints or []

    def synthesize(
        self,
        strategy: Strategy | None = None,
        callbacks: list[Callback] | None = None,
    ) -> SynthesisResult:
        """Run synthesis with the given strategy."""
        strategy = strategy or TestConvergence()
        return strategy.run(
            agent=self.agent,
            spec=self.spec,
            env=self.env,
            constraints=self.constraints,
            callbacks=callbacks or [],
        )
