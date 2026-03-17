"""Tests for TrainingCurveCallback (Feature 1: Training Curves)."""

from __future__ import annotations

import json
import os
import tempfile

from chimera.training.callbacks import TrainingCurveCallback
from chimera.training.strategies.base import EpochResult, SynthesisResult


def _epoch(epoch: int, pass_rate: float, passed: int, total: int, cost: float = 0.01) -> EpochResult:
    """Create a mock EpochResult."""
    return EpochResult(
        epoch=epoch,
        pass_rate=pass_rate,
        passed=passed,
        total=total,
        agent_output="",
        cost=cost,
    )


def _synthesis(converged: bool = True, iterations: int = 5) -> SynthesisResult:
    """Create a mock SynthesisResult."""
    return SynthesisResult(
        converged=converged,
        iterations=iterations,
        total_cost=0.05,
        best_pass_rate=1.0,
    )


class TestCurveRecordsEpochs:
    def test_curve_records_epochs(self) -> None:
        curve = TrainingCurveCallback()
        epochs = [
            _epoch(1, 0.2, 2, 10),
            _epoch(2, 0.5, 5, 10),
            _epoch(3, 0.8, 8, 10),
        ]
        for e in epochs:
            curve.on_epoch_end(e)

        assert len(curve.epochs) == 3
        assert curve.epochs[0].pass_rate == 0.2
        assert curve.epochs[1].pass_rate == 0.5
        assert curve.epochs[2].pass_rate == 0.8


class TestCurveSummaryFormat:
    def test_curve_summary_format(self) -> None:
        curve = TrainingCurveCallback()
        curve.on_epoch_end(_epoch(1, 0.5, 5, 10, cost=0.0123))
        curve.on_epoch_end(_epoch(2, 1.0, 10, 10, cost=0.0456))

        summary = curve.summary()
        assert "Epoch  1" in summary
        assert "Epoch  2" in summary
        assert "50.0%" in summary
        assert "100.0%" in summary
        assert "$0.0123" in summary
        assert "$0.0456" in summary
        # Check the visual bar is present
        assert "|" in summary
        assert "#" in summary


class TestCurveDiagnosePlateau:
    def test_curve_diagnose_plateau(self) -> None:
        curve = TrainingCurveCallback()
        # 4 epochs with the same pass_rate -> plateau
        for i in range(1, 5):
            curve.on_epoch_end(_epoch(i, 0.6, 6, 10))

        warnings = curve.diagnose()
        assert any("Plateau" in w for w in warnings)

    def test_no_plateau_with_changing_rates(self) -> None:
        curve = TrainingCurveCallback()
        curve.on_epoch_end(_epoch(1, 0.2, 2, 10))
        curve.on_epoch_end(_epoch(2, 0.4, 4, 10))
        curve.on_epoch_end(_epoch(3, 0.6, 6, 10))

        warnings = curve.diagnose()
        assert not any("Plateau" in w for w in warnings)


class TestCurveDiagnoseOscillation:
    def test_curve_diagnose_oscillation(self) -> None:
        curve = TrainingCurveCallback()
        # Alternating: up, down, up, down, up
        rates = [0.3, 0.6, 0.3, 0.6, 0.3]
        for i, r in enumerate(rates, 1):
            passed = int(r * 10)
            curve.on_epoch_end(_epoch(i, r, passed, 10))

        warnings = curve.diagnose()
        assert any("Oscillation" in w for w in warnings)

    def test_no_oscillation_with_steady_increase(self) -> None:
        curve = TrainingCurveCallback()
        for i in range(1, 6):
            curve.on_epoch_end(_epoch(i, i * 0.2, i * 2, 10))

        warnings = curve.diagnose()
        assert not any("Oscillation" in w for w in warnings)


class TestCurveDiagnoseInstant:
    def test_curve_diagnose_instant(self) -> None:
        curve = TrainingCurveCallback()
        curve.on_epoch_end(_epoch(1, 1.0, 10, 10))

        warnings = curve.diagnose()
        assert any("Instant convergence" in w for w in warnings)

    def test_no_instant_when_first_epoch_not_perfect(self) -> None:
        curve = TrainingCurveCallback()
        curve.on_epoch_end(_epoch(1, 0.9, 9, 10))

        warnings = curve.diagnose()
        assert not any("Instant convergence" in w for w in warnings)


class TestCurveDiagnoseCostExplosion:
    def test_curve_diagnose_cost_explosion(self) -> None:
        curve = TrainingCurveCallback()
        curve.on_epoch_end(_epoch(1, 0.5, 5, 10, cost=0.01))
        curve.on_epoch_end(_epoch(2, 0.6, 6, 10, cost=0.05))  # >2x increase

        warnings = curve.diagnose()
        assert any("Cost explosion" in w for w in warnings)

    def test_no_cost_explosion_with_gradual_increase(self) -> None:
        curve = TrainingCurveCallback()
        curve.on_epoch_end(_epoch(1, 0.5, 5, 10, cost=0.01))
        curve.on_epoch_end(_epoch(2, 0.6, 6, 10, cost=0.015))

        warnings = curve.diagnose()
        assert not any("Cost explosion" in w for w in warnings)


class TestCurveToDict:
    def test_curve_to_dict(self) -> None:
        curve = TrainingCurveCallback()
        curve.on_epoch_end(_epoch(1, 0.5, 5, 10, cost=0.01))
        curve.on_epoch_end(_epoch(2, 1.0, 10, 10, cost=0.02))

        data = curve.to_dict()
        assert len(data) == 2
        assert data[0] == {
            "epoch": 1,
            "pass_rate": 0.5,
            "passed": 5,
            "total": 10,
            "cost": 0.01,
        }
        assert data[1] == {
            "epoch": 2,
            "pass_rate": 1.0,
            "passed": 10,
            "total": 10,
            "cost": 0.02,
        }


class TestCurveOutputFile:
    def test_curve_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "training.json")
            curve = TrainingCurveCallback(output_path=output_path)
            curve.on_epoch_end(_epoch(1, 0.5, 5, 10, cost=0.01))
            curve.on_epoch_end(_epoch(2, 1.0, 10, 10, cost=0.02))
            curve.on_synthesis_end(_synthesis())

            assert os.path.exists(output_path)
            with open(output_path) as f:
                data = json.load(f)
            assert len(data) == 2
            assert data[0]["epoch"] == 1
            assert data[1]["epoch"] == 2

    def test_no_file_when_no_path(self) -> None:
        curve = TrainingCurveCallback()
        curve.on_epoch_end(_epoch(1, 0.5, 5, 10))
        # Should not raise
        curve.on_synthesis_end(_synthesis())
