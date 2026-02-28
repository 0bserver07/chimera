"""High-level orchestrator for code synthesis.

The :class:`Trainer` brings together a :class:`~chimera.training.spec.Spec`,
an :class:`~chimera.core.agent.Agent`, an
:class:`~chimera.env.base.Environment`, an optional
:class:`~chimera.training.architecture.Architecture`, and a set of
:class:`~chimera.training.constraint.Constraint` objects, then delegates the
actual synthesis loop to a :class:`~chimera.training.strategies.base.Strategy`.
"""

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

    The main orchestrator for code synthesis.  Call :meth:`synthesize` to
    kick off a synthesis run with a chosen strategy (defaults to
    :class:`~chimera.training.strategies.convergence.TestConvergence`).

    Attributes:
        architecture: Optional scaffold describing the target codebase layout.
        spec: The specification that defines *what* to synthesize.
        agent: The coding agent that performs generation.
        env: Execution environment (sandbox) for running code and tests.
        constraints: Guard-rails applied during synthesis (e.g. token budgets).
    """

    def __init__(
        self,
        spec: Spec,
        agent: Agent,
        env: Environment,
        architecture: Architecture | None = None,
        constraints: list[Constraint] | None = None,
    ) -> None:
        """Initialise the Trainer.

        Args:
            spec: Specification describing the synthesis target.
            agent: Agent to use for code generation.
            env: Environment in which generated code runs.
            architecture: Optional high-level codebase blueprint.
            constraints: Optional list of constraints applied during synthesis.
        """
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
        """Run the synthesis loop with the given strategy.

        Args:
            strategy: Strategy implementation to use.  Defaults to
                :class:`~chimera.training.strategies.convergence.TestConvergence`.
            callbacks: Optional list of callbacks invoked at each iteration
                (useful for logging or early stopping).

        Returns:
            A :class:`~chimera.training.strategies.base.SynthesisResult`
            containing the generated artefacts, pass/fail status, and
            metadata.
        """
        strategy = strategy or TestConvergence()
        return strategy.run(
            agent=self.agent,
            spec=self.spec,
            env=self.env,
            constraints=self.constraints,
            callbacks=callbacks or [],
        )
