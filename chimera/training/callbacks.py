from __future__ import annotations

from chimera.training.strategies.base import Callback, EpochResult, SynthesisResult


class CheckpointCallback(Callback):
    """Save a checkpoint every N epochs."""

    def __init__(self, every: int = 5) -> None:
        self.every = every
        self.epochs_seen: list[EpochResult] = []

    def on_epoch_end(self, epoch: int | EpochResult, result: EpochResult | None = None) -> bool:
        er = result if result is not None else epoch
        self.epochs_seen.append(er)
        return True

    def on_synthesis_start(self) -> None:
        self.epochs_seen = []

    def on_synthesis_end(self, result: SynthesisResult) -> None:
        pass


class CostLimitCallback(Callback):
    """Abort if total cost exceeds a limit."""

    def __init__(self, max_cost: float = 10.0) -> None:
        self.max_cost = max_cost
        self.total_cost = 0.0
        self.exceeded = False

    def on_epoch_end(self, epoch: int | EpochResult, result: EpochResult | None = None) -> bool:
        er = result if result is not None else epoch
        self.total_cost += er.cost
        if self.total_cost > self.max_cost:
            self.exceeded = True
        return True

    def on_synthesis_start(self) -> None:
        self.total_cost = 0.0
        self.exceeded = False

    def on_synthesis_end(self, result: SynthesisResult) -> None:
        pass


class ProgressCallback(Callback):
    """Track progress for display."""

    def __init__(self) -> None:
        self.epochs: list[EpochResult] = []

    def on_epoch_end(self, epoch: int | EpochResult, result: EpochResult | None = None) -> bool:
        er = result if result is not None else epoch
        self.epochs.append(er)
        return True

    def on_synthesis_start(self) -> None:
        self.epochs = []

    def on_synthesis_end(self, result: SynthesisResult) -> None:
        pass


class ProgressBar(Callback):
    """Simple text-based progress bar for synthesis."""

    def __init__(self, max_iterations: int = 50) -> None:
        self.max_iterations = max_iterations
        self._started = False

    def on_synthesis_start(self) -> None:
        self._started = True
        print(f"Synthesis starting (max {self.max_iterations} iterations)")

    def on_epoch_end(self, epoch: int | EpochResult, result: EpochResult | None = None) -> bool:
        er = result if result is not None else epoch
        bar_len = 30
        filled = int(bar_len * er.pass_rate)
        bar = "\u2588" * filled + "\u2591" * (bar_len - filled)
        print(
            f"  [{bar}] Epoch {er.epoch}: "
            f"{er.passed}/{er.total} ({er.pass_rate:.0%})"
        )
        return True

    def on_synthesis_end(self, result: SynthesisResult) -> None:
        status = "CONVERGED" if result.converged else "STOPPED"
        print(
            f"Synthesis {status} after {result.iterations} iterations "
            f"(best: {result.best_pass_rate:.0%})"
        )


# ---------------------------------------------------------------------------
# Phase 6-8 backward-compatible aliases
# ---------------------------------------------------------------------------


class CostLimit(Callback):
    """Cost limit callback (Phase 6-8 API).

    Tracks cumulative cost across epochs and returns False from
    on_epoch_end when the budget is exceeded.
    """

    def __init__(self, max_cost: float = 10.0) -> None:
        self.max_cost = max_cost
        self._total_cost = 0.0

    def on_epoch_end(self, epoch: int | EpochResult, result: EpochResult | None = None) -> bool:
        er = result if result is not None else epoch
        self._total_cost += er.cost
        return self._total_cost < self.max_cost

    def on_synthesis_start(self) -> None:
        self._total_cost = 0.0


class EpochCheckpoint(Callback):
    """Record checkpoint IDs at regular intervals (Phase 6-8 API)."""

    def __init__(self, every: int = 1) -> None:
        self.every = every
        self.checkpoints: list[str] = []

    def on_epoch_end(self, epoch: int | EpochResult, result: EpochResult | None = None) -> bool:
        er = result if result is not None else epoch
        epoch_num = epoch if isinstance(epoch, int) else er.epoch
        if epoch_num % self.every == 0 and er.checkpoint_id is not None:
            self.checkpoints.append(er.checkpoint_id)
        return True


class HistoryRecorder(Callback):
    """Records synthesis history for inspection (Phase 6-8 API)."""

    def __init__(self) -> None:
        self.started: bool = False
        self.finished: bool = False
        self.epochs: list[EpochResult] = []
        self.final_result: SynthesisResult | None = None

    def on_synthesis_start(self) -> None:
        self.started = True
        self.finished = False
        self.epochs = []
        self.final_result = None

    def on_epoch_end(self, epoch: int | EpochResult, result: EpochResult | None = None) -> bool:
        er = result if result is not None else epoch
        self.epochs.append(er)
        return True

    def on_synthesis_end(self, result: SynthesisResult) -> None:
        self.finished = True
        self.final_result = result
