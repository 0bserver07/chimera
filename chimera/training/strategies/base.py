from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EpochResult:
    epoch: int
    pass_rate: float
    passed: int
    total: int
    agent_output: str
    checkpoint_id: str | None = None
    improved: bool = False
    cost: float = 0.0


@dataclass
class SynthesisResult:
    converged: bool
    iterations: int
    total_cost: float
    best_pass_rate: float
    history: list[EpochResult] = field(default_factory=list)
    failure_reason: str | None = None


class Callback:
    def on_synthesis_start(self, **kwargs: Any) -> None:
        pass

    def on_epoch_start(self, epoch: int, **kwargs: Any) -> None:
        pass

    def on_epoch_end(self, epoch: int, result: EpochResult, **kwargs: Any) -> bool:
        return True

    def on_synthesis_end(self, result: SynthesisResult, **kwargs: Any) -> None:
        pass


class Strategy(ABC):
    @abstractmethod
    def run(
        self,
        agent: Any,
        spec: Any,
        env: Any,
        constraints: list[Any] | None = None,
        callbacks: list[Callback] | None = None,
    ) -> SynthesisResult: ...
