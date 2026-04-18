from __future__ import annotations

import json

from chimera.training.strategies.base import Callback, EpochResult, SynthesisResult


def _to_epoch_result(
    epoch: int | EpochResult, result: EpochResult | None
) -> EpochResult:
    """Normalize the two-arg / one-arg on_epoch_end signatures.

    Callers pass either ``(epoch_int, epoch_result)`` (two-arg form) or
    ``(epoch_result, None)`` (one-arg form). This helper returns the
    ``EpochResult`` regardless, raising ``TypeError`` on malformed calls.
    """
    if result is not None:
        return result
    if isinstance(epoch, EpochResult):
        return epoch
    raise TypeError(
        f"on_epoch_end expected an EpochResult; got epoch={epoch!r} with "
        "result=None. Pass either (epoch_int, EpochResult) or (EpochResult,)."
    )


class CheckpointCallback(Callback):
    """Save a checkpoint every N epochs."""

    def __init__(self, every: int = 5) -> None:
        self.every = every
        self.epochs_seen: list[EpochResult] = []

    def on_epoch_end(self, epoch: int | EpochResult, result: EpochResult | None = None) -> bool:
        er = _to_epoch_result(epoch, result)
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
        er = _to_epoch_result(epoch, result)
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
        er = _to_epoch_result(epoch, result)
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
        er = _to_epoch_result(epoch, result)
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
        er = _to_epoch_result(epoch, result)
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
        er = _to_epoch_result(epoch, result)
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
        er = _to_epoch_result(epoch, result)
        self.epochs.append(er)
        return True

    def on_synthesis_end(self, result: SynthesisResult) -> None:
        self.finished = True
        self.final_result = result


class TrainingCurveCallback(Callback):
    """Log per-epoch metrics and diagnose training patterns.

    Records every ``EpochResult`` and exposes helpers to summarise
    progress, detect common pathologies (plateau, oscillation, cost
    explosion, instant convergence), and export data as JSON.

    Args:
        output_path: Optional filesystem path.  When set, ``to_dict()``
            is written there as JSON at synthesis end.
    """

    def __init__(self, output_path: str | None = None) -> None:
        self.epochs: list[EpochResult] = []
        self._output_path = output_path

    def on_epoch_end(self, epoch: int | EpochResult, result: EpochResult | None = None) -> bool:
        """Record the epoch result."""
        er = _to_epoch_result(epoch, result)
        self.epochs.append(er)
        return True

    def on_synthesis_end(self, result: SynthesisResult) -> None:
        """Write JSON output if an output path was configured."""
        if self._output_path:
            self._write_json()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Text summary with per-epoch pass_rate and cost.

        Returns:
            A multi-line string with one line per epoch showing a
            visual progress bar, the pass rate, and the dollar cost.
        """
        lines = []
        for e in self.epochs:
            bar = "#" * int(e.pass_rate * 20)
            lines.append(
                f"  Epoch {e.epoch:2d}: {e.pass_rate:5.1%} |{bar:<20}| ${e.cost:.4f}"
            )
        return "\n".join(lines)

    def diagnose(self) -> list[str]:
        """Detect common training pathologies.

        Checks for:
        - **Plateau**: ``pass_rate`` unchanged for 3+ consecutive epochs.
        - **Oscillation**: ``pass_rate`` alternates up/down for 4+
          consecutive epochs.
        - **Cost explosion**: per-epoch cost increases >2x between
          consecutive epochs.
        - **Instant convergence**: ``pass_rate`` reaches 1.0 on
          epoch 1 (may indicate a trivial spec or the agent cheating).

        Returns:
            A list of human-readable warning strings.  Empty when no
            issues are detected.
        """
        warnings: list[str] = []
        rates = [e.pass_rate for e in self.epochs]
        costs = [e.cost for e in self.epochs]

        # Instant convergence
        if rates and rates[0] == 1.0:
            warnings.append(
                "Instant convergence: pass_rate=1.0 on epoch 1. "
                "Spec may be too easy or agent may be cheating."
            )

        # Plateau: 3+ consecutive epochs with the same pass_rate
        if len(rates) >= 3:
            run = 1
            for i in range(1, len(rates)):
                if rates[i] == rates[i - 1]:
                    run += 1
                    if run >= 3:
                        warnings.append(
                            f"Plateau detected: pass_rate unchanged at "
                            f"{rates[i]:.1%} for {run} consecutive epochs "
                            f"(epochs {i - run + 2}-{i + 1}). "
                            f"Try a different strategy or model."
                        )
                        break
                else:
                    run = 1

        # Oscillation: 4+ consecutive epochs where direction alternates
        if len(rates) >= 4:
            alternating = 1
            for i in range(2, len(rates)):
                prev_dir = rates[i - 1] - rates[i - 2]
                curr_dir = rates[i] - rates[i - 1]
                if prev_dir != 0 and curr_dir != 0 and (
                    (prev_dir > 0 and curr_dir < 0)
                    or (prev_dir < 0 and curr_dir > 0)
                ):
                    alternating += 1
                    if alternating >= 4:
                        warnings.append(
                            "Oscillation detected: pass_rate alternating "
                            f"up/down for {alternating}+ epochs. "
                            "Agent may be fixing one test while breaking another."
                        )
                        break
                else:
                    alternating = 1

        # Cost explosion: any consecutive pair where cost increases >2x
        for i in range(1, len(costs)):
            if costs[i - 1] > 0 and costs[i] > 2 * costs[i - 1]:
                warnings.append(
                    f"Cost explosion: epoch {i + 1} cost (${costs[i]:.4f}) "
                    f"is >{2}x epoch {i} cost (${costs[i - 1]:.4f}). "
                    "Context may be bloated — consider compaction."
                )
                break

        return warnings

    def to_dict(self) -> list[dict[str, float | int]]:
        """JSON-serializable list of epoch data.

        Returns:
            A list of dicts, one per epoch, with keys ``epoch``,
            ``pass_rate``, ``passed``, ``total``, and ``cost``.
        """
        return [
            {
                "epoch": e.epoch,
                "pass_rate": e.pass_rate,
                "passed": e.passed,
                "total": e.total,
                "cost": e.cost,
            }
            for e in self.epochs
        ]

    def _write_json(self) -> None:
        """Write ``to_dict()`` to ``self._output_path``."""
        if self._output_path is None:
            return
        with open(self._output_path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
