from __future__ import annotations

import os
import tempfile

from chimera.training.oracle import OracleCallback
from chimera.training.strategies.base import EpochResult


def _make_epoch(epoch: int = 1, pass_rate: float = 1.0, agent_output: str = "def add(a, b): return a + b") -> EpochResult:
    """Create a mock EpochResult for testing."""
    total = 10
    passed = int(pass_rate * total)
    return EpochResult(
        epoch=epoch,
        pass_rate=pass_rate,
        passed=passed,
        total=total,
        agent_output=agent_output,
    )


def test_oracle_generates_on_full_pass():
    """OracleCallback generates tests when pass_rate == 1.0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        oracle = OracleCallback(
            tests_dir=tmpdir,
            mode="property",
            max_new_tests_per_epoch=3,
        )
        result = _make_epoch(epoch=1, pass_rate=1.0)
        oracle.on_epoch_end(result)

        assert len(oracle.generated_tests) > 0
        assert "def test_" in oracle.generated_tests[0]


def test_oracle_skips_on_failure():
    """OracleCallback does not generate tests when pass_rate < 1.0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        oracle = OracleCallback(
            tests_dir=tmpdir,
            mode="property",
            max_new_tests_per_epoch=3,
        )
        result = _make_epoch(epoch=1, pass_rate=0.8)
        oracle.on_epoch_end(result)

        assert len(oracle.generated_tests) == 0


def test_oracle_writes_to_dir():
    """Generated tests appear as files in tests_dir."""
    with tempfile.TemporaryDirectory() as tmpdir:
        oracle = OracleCallback(
            tests_dir=tmpdir,
            mode="property",
            max_new_tests_per_epoch=3,
        )
        result = _make_epoch(epoch=1, pass_rate=1.0)
        oracle.on_epoch_end(result)

        files = os.listdir(tmpdir)
        assert len(files) > 0
        assert any(f.startswith("test_oracle_epoch") for f in files)
        # Verify file content
        path = os.path.join(tmpdir, files[0])
        with open(path) as f:
            content = f.read()
        assert "def test_" in content


def test_oracle_max_per_epoch():
    """OracleCallback respects max_new_tests_per_epoch limit."""
    with tempfile.TemporaryDirectory() as tmpdir:
        oracle = OracleCallback(
            tests_dir=tmpdir,
            mode="property",
            max_new_tests_per_epoch=2,
        )
        result = _make_epoch(epoch=1, pass_rate=1.0)
        oracle.on_epoch_end(result)

        assert len(oracle.generated_tests) <= 2


def test_oracle_accumulates():
    """generated_tests list grows across multiple epochs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        oracle = OracleCallback(
            tests_dir=tmpdir,
            mode="property",
            max_new_tests_per_epoch=1,
        )
        # Epoch 1 - all pass
        oracle.on_epoch_end(_make_epoch(epoch=1, pass_rate=1.0))
        count_after_first = len(oracle.generated_tests)
        assert count_after_first > 0

        # Epoch 2 - all pass again
        oracle.on_epoch_end(_make_epoch(epoch=2, pass_rate=1.0))
        count_after_second = len(oracle.generated_tests)
        assert count_after_second > count_after_first

        # Verify files accumulate on disk too
        files = os.listdir(tmpdir)
        assert len(files) == count_after_second
