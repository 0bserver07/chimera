"""Concrete callback implementations for synthesis control and monitoring."""

from __future__ import annotations

from chimera.training.strategies.base import Callback, EpochResult, SynthesisResult


class CostLimit(Callback):
    """Stop synthesis if total cost exceeds a limit."""

    def __init__(self, max_cost: float) -> None:
        self.max_cost = max_cost
        self._total_cost = 0.0

    def on_epoch_end(self, epoch: int, result: EpochResult, **kwargs) -> bool:
        self._total_cost += result.cost
        if self._total_cost >= self.max_cost:
            return False  # Stop synthesis
        return True


class EpochCheckpoint(Callback):
    """Record checkpoints at regular intervals."""

    def __init__(self, every: int = 1) -> None:
        self.every = every
        self.checkpoints: list[str] = []

    def on_epoch_end(self, epoch: int, result: EpochResult, **kwargs) -> bool:
        if epoch % self.every == 0 and result.checkpoint_id:
            self.checkpoints.append(result.checkpoint_id)
        return True


class HistoryRecorder(Callback):
    """Record synthesis history for analysis."""

    def __init__(self) -> None:
        self.epochs: list[EpochResult] = []
        self.started = False
        self.finished = False
        self.final_result: SynthesisResult | None = None

    def on_synthesis_start(self, **kwargs) -> None:
        self.started = True

    def on_epoch_end(self, epoch: int, result: EpochResult, **kwargs) -> bool:
        self.epochs.append(result)
        return True

    def on_synthesis_end(self, result: SynthesisResult, **kwargs) -> None:
        self.finished = True
        self.final_result = result
