from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.training.constraint import Constraint
    from chimera.training.spec import Spec


@dataclass
class EpochResult:
    """Result of a single training epoch."""

    epoch: int
    pass_rate: float
    passed: int
    total: int
    agent_output: str
    improved: bool
    cost: float = 0.0


@dataclass
class SynthesisResult:
    """Final result of a synthesis run."""

    converged: bool
    iterations: int
    total_cost: float
    best_pass_rate: float
    history: list[EpochResult] = field(default_factory=list)
    failure_reason: str | None = None


class Callback(ABC):
    """Observer for synthesis events."""

    def on_synthesis_start(self) -> None:
        """Called when synthesis begins."""

    def on_epoch_end(self, epoch: EpochResult) -> None:
        """Called after each epoch."""

    def on_synthesis_end(self, result: SynthesisResult) -> None:
        """Called when synthesis completes."""


class Strategy(ABC):
    """Abstract base for training strategies.

    A strategy controls how an agent is driven through test-guided
    synthesis: how many iterations, when to stop, when to rollback, etc.
    """

    @abstractmethod
    def run(
        self,
        agent: Agent,
        spec: Spec,
        env: Environment,
        constraints: list[Constraint] | None = None,
        callbacks: list[Callback] | None = None,
    ) -> SynthesisResult:
        """Execute the strategy and return synthesis results."""
