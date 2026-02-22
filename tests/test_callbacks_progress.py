"""Tests for the ProgressBar callback."""
from __future__ import annotations

from chimera.training.callbacks import ProgressBar
from chimera.training.strategies.base import EpochResult, SynthesisResult


class TestProgressBar:
    def test_on_synthesis_start_prints(self, capsys):
        cb = ProgressBar(max_iterations=25)
        cb.on_synthesis_start()
        captured = capsys.readouterr()
        assert "Synthesis starting" in captured.out
        assert "25" in captured.out
        assert cb._started

    def test_on_epoch_end_prints_progress(self, capsys):
        cb = ProgressBar()
        epoch = EpochResult(
            epoch=3,
            pass_rate=0.8,
            passed=8,
            total=10,
            agent_output="",
            improved=True,
        )
        cb.on_epoch_end(epoch)
        captured = capsys.readouterr()
        assert "Epoch 3" in captured.out
        assert "8/10" in captured.out
        assert "80%" in captured.out
        # Check bar characters are present
        assert "\u2588" in captured.out  # filled block
        assert "\u2591" in captured.out  # empty block

    def test_on_synthesis_end_converged(self, capsys):
        cb = ProgressBar()
        result = SynthesisResult(
            converged=True,
            iterations=15,
            total_cost=2.5,
            best_pass_rate=1.0,
        )
        cb.on_synthesis_end(result)
        captured = capsys.readouterr()
        assert "CONVERGED" in captured.out
        assert "15" in captured.out
        assert "100%" in captured.out

    def test_on_synthesis_end_stopped(self, capsys):
        cb = ProgressBar()
        result = SynthesisResult(
            converged=False,
            iterations=50,
            total_cost=10.0,
            best_pass_rate=0.6,
        )
        cb.on_synthesis_end(result)
        captured = capsys.readouterr()
        assert "STOPPED" in captured.out
        assert "50" in captured.out
        assert "60%" in captured.out
