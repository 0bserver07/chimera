from __future__ import annotations

from chimera.training.strategies.base import Callback, EpochResult, SynthesisResult


class CheckpointCallback(Callback):
    """Save a checkpoint every N epochs."""

    def __init__(self, every: int = 5) -> None:
        self.every = every
        self.epochs_seen: list[EpochResult] = []

    def on_epoch_end(self, epoch: EpochResult) -> None:
        self.epochs_seen.append(epoch)

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

    def on_epoch_end(self, epoch: EpochResult) -> None:
        self.total_cost += epoch.cost
        if self.total_cost > self.max_cost:
            self.exceeded = True

    def on_synthesis_start(self) -> None:
        self.total_cost = 0.0
        self.exceeded = False

    def on_synthesis_end(self, result: SynthesisResult) -> None:
        pass


class ProgressCallback(Callback):
    """Track progress for display."""

    def __init__(self) -> None:
        self.epochs: list[EpochResult] = []

    def on_epoch_end(self, epoch: EpochResult) -> None:
        self.epochs.append(epoch)

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

    def on_epoch_end(self, epoch: EpochResult) -> None:
        bar_len = 30
        filled = int(bar_len * epoch.pass_rate)
        bar = "\u2588" * filled + "\u2591" * (bar_len - filled)
        print(
            f"  [{bar}] Epoch {epoch.epoch}: "
            f"{epoch.passed}/{epoch.total} ({epoch.pass_rate:.0%})"
        )

    def on_synthesis_end(self, result: SynthesisResult) -> None:
        status = "CONVERGED" if result.converged else "STOPPED"
        print(
            f"Synthesis {status} after {result.iterations} iterations "
            f"(best: {result.best_pass_rate:.0%})"
        )
