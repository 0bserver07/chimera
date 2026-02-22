"""Trainer — the top-level orchestrator for code synthesis.

Like Keras's model, but for code synthesis:
    trainer.synthesize() = model.fit()
"""

from __future__ import annotations

from chimera.core.agent import Agent
from chimera.env.base import Environment
from chimera.training.architecture import Architecture
from chimera.training.constraint import Constraint
from chimera.training.spec import Spec
from chimera.training.strategies.base import Callback, Strategy, SynthesisResult
from chimera.training.strategies.convergence import TestConvergence


class Trainer:
    """The top-level orchestrator. Like Keras's model, but for code synthesis.

    Trainer = Architecture + Spec + Agent + Environment.
    trainer.synthesize() = model.fit()
    """

    def __init__(
        self,
        spec: Spec,
        agent: Agent,
        env: Environment,
        architecture: Architecture | None = None,
        constraints: list[Constraint] | None = None,
    ) -> None:
        self.spec = spec
        self.agent = agent
        self.env = env
        self.architecture = architecture
        self.constraints = constraints or []

    def synthesize(
        self,
        strategy: Strategy | None = None,
        callbacks: list[Callback] | None = None,
    ) -> SynthesisResult:
        """Synthesize a codebase from the spec.

        This is the core verb. Like model.fit() in Keras.
        """
        strategy = strategy or TestConvergence()
        callbacks = callbacks or []

        return strategy.run(
            agent=self.agent,
            spec=self.spec,
            env=self.env,
            constraints=self.constraints,
            callbacks=callbacks,
        )
