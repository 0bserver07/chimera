"""Tests for ``chimera.shrew.benchmarks.terminal_bench`` (agent S3, wave 9).

Covers:

* :class:`TerminalBench` schema loading + limit, prompt synthesis, and
  exit-code-based scoring (pass via ``true``, fail via ``false``,
  timeout via ``sleep``).
* The dataset-availability helpers and env-var override.
* The CLI dispatch path: no longer returns the legacy "not yet wired"
  message; missing dataset returns the new setup hint with exit 3;
  dataset-present round-trip with a patched agent.

All tests are stdlib-only and never touch the network. Subprocess
calls use posix utilities (``true``, ``false``, ``test``) that are
always available on the test runner.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tb_dataset(tmp_path: Path) -> Path:
    """Stage a tiny Terminal-Bench dataset under ``tmp_path``.

    Three tasks: one passing (``true``), one failing (``false``), and
    one with a per-task working directory + a ``test -f`` verify.
    """
    # Stage a per-task workdir for the third task.
    (tmp_path / "tasks" / "tb-003").mkdir(parents=True)
    (tmp_path / "tasks" / "tb-003" / "result.txt").write_text("OK\n")

    tasks = [
        {
            "task_id": "tb-001",
            "instruction": "Print hello.",
            "verify_command": "true",
        },
        {
            "task_id": "tb-002",
            "instruction": "Set up a thing.",
            "verify_command": "false",
        },
        {
            "task_id": "tb-003",
            "instruction": "Stage a result.",
            "verify_command": "test -f result.txt && grep -q OK result.txt",
            "task_dir": "tb-003",
        },
    ]
    (tmp_path / "tasks.json").write_text(json.dumps(tasks))
    return tmp_path


# ---------------------------------------------------------------------------
# Module / ABC smoke tests
# ---------------------------------------------------------------------------


def test_terminal_bench_module_imports() -> None:
    """Module imports without dragging in provider SDKs."""
    from chimera.shrew.benchmarks import terminal_bench

    assert terminal_bench.TerminalBench is not None
    assert terminal_bench.dataset_available is not None
    assert terminal_bench.setup_hint is not None


def test_terminal_bench_subclasses_benchmark() -> None:
    """The adapter implements the standard :class:`Benchmark` ABC."""
    from chimera.eval.harness import Benchmark
    from chimera.shrew.benchmarks.terminal_bench import TerminalBench

    assert issubclass(TerminalBench, Benchmark)


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------


def test_tb_dataset_absent_returns_empty(tmp_path: Path) -> None:
    """When ``tasks.json`` is missing, ``tasks()`` returns ``[]``."""
    from chimera.shrew.benchmarks.terminal_bench import (
        TerminalBench,
        dataset_available,
    )

    bench = TerminalBench(dataset_path=str(tmp_path))
    assert dataset_available(tmp_path) is False
    assert bench.tasks() == []


def test_tb_dataset_available_true(tb_dataset: Path) -> None:
    """``dataset_available`` is ``True`` once ``tasks.json`` is staged."""
    from chimera.shrew.benchmarks.terminal_bench import dataset_available

    assert dataset_available(tb_dataset) is True


def test_tb_loads_all_tasks(tb_dataset: Path) -> None:
    """Without a filter, every task is returned with synthesised prompt."""
    from chimera.shrew.benchmarks.terminal_bench import TerminalBench

    bench = TerminalBench(dataset_path=str(tb_dataset))
    tasks = bench.tasks()
    assert len(tasks) == 3
    assert {t["id"] for t in tasks} == {"tb-001", "tb-002", "tb-003"}
    # Prompts are synthesised with the verify-grader notice.
    assert all("verify command" in t["prompt"] for t in tasks)


def test_tb_limit(tb_dataset: Path) -> None:
    """``limit`` caps the number of tasks returned."""
    from chimera.shrew.benchmarks.terminal_bench import TerminalBench

    bench = TerminalBench(dataset_path=str(tb_dataset), limit=2)
    assert len(bench.tasks()) == 2


def test_tb_name(tb_dataset: Path) -> None:
    """The benchmark name is ``terminal-bench`` (no filter suffix today)."""
    from chimera.shrew.benchmarks.terminal_bench import TerminalBench

    assert TerminalBench(dataset_path=str(tb_dataset)).name() == "terminal-bench"


def test_tb_setup_hint_mentions_env_var(tmp_path: Path) -> None:
    """The setup hint surfaces the env-var override and expected file."""
    from chimera.shrew.benchmarks.terminal_bench import setup_hint

    hint = setup_hint(tmp_path)
    assert "tasks.json" in hint
    assert "CHIMERA_TERMINAL_BENCH_PATH" in hint


def test_tb_env_var_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``CHIMERA_TERMINAL_BENCH_PATH`` overrides the default dataset root."""
    from chimera.shrew.benchmarks.terminal_bench import default_dataset_path

    monkeypatch.setenv("CHIMERA_TERMINAL_BENCH_PATH", str(tmp_path))
    assert default_dataset_path() == tmp_path


# ---------------------------------------------------------------------------
# Scoring (subprocess)
# ---------------------------------------------------------------------------


def test_tb_evaluate_pass_via_true(tb_dataset: Path) -> None:
    """The ``true`` verify command makes the task pass."""
    from chimera.shrew.benchmarks.terminal_bench import TerminalBench

    bench = TerminalBench(dataset_path=str(tb_dataset))
    task = next(t for t in bench.tasks() if t["id"] == "tb-001")
    assert bench.evaluate(task, "ignored agent output", env=None) is True


def test_tb_evaluate_fail_via_false(tb_dataset: Path) -> None:
    """The ``false`` verify command makes the task fail."""
    from chimera.shrew.benchmarks.terminal_bench import TerminalBench

    bench = TerminalBench(dataset_path=str(tb_dataset))
    task = next(t for t in bench.tasks() if t["id"] == "tb-002")
    assert bench.evaluate(task, "ignored", env=None) is False


def test_tb_evaluate_uses_task_dir(tb_dataset: Path) -> None:
    """When ``task_dir`` is staged, the verify command runs from it."""
    from chimera.shrew.benchmarks.terminal_bench import TerminalBench

    bench = TerminalBench(dataset_path=str(tb_dataset))
    task = next(t for t in bench.tasks() if t["id"] == "tb-003")
    # The verify command is ``test -f result.txt && grep -q OK ...``,
    # which only succeeds inside the staged tasks/tb-003/ workdir.
    assert bench.evaluate(task, "ignored", env=None) is True


def test_tb_evaluate_uses_env_workdir(tb_dataset: Path, tmp_path: Path) -> None:
    """``env.workdir`` takes precedence over the staged ``task_dir``."""
    from chimera.shrew.benchmarks.terminal_bench import TerminalBench

    bench = TerminalBench(dataset_path=str(tb_dataset))
    task = next(t for t in bench.tasks() if t["id"] == "tb-003")

    # Stage the same expected file under a different workdir; the verify
    # command must succeed there too — proving env.workdir is honoured.
    workdir = tmp_path / "alt"
    workdir.mkdir()
    (workdir / "result.txt").write_text("OK\n")
    env = MagicMock(workdir=str(workdir), spec=["workdir"])

    assert bench.evaluate(task, "ignored", env=env) is True


def test_tb_evaluate_missing_verify_returns_false() -> None:
    """A task with no verify_command fails-closed."""
    from chimera.shrew.benchmarks.terminal_bench import TerminalBench

    bench = TerminalBench()
    assert bench.evaluate({"task_id": "x"}, "ignored", env=None) is False


def test_tb_evaluate_timeout_returns_false() -> None:
    """A verify command that exceeds ``timeout_s`` fails-closed."""
    from chimera.shrew.benchmarks.terminal_bench import TerminalBench

    bench = TerminalBench()
    task = {
        "task_id": "x",
        "verify_command": "sleep 5",
        "timeout_s": 1,
    }
    assert bench.evaluate(task, "ignored", env=None) is False


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------


def _make_args(**overrides: Any) -> argparse.Namespace:
    """Build a minimal argparse.Namespace for ``dispatch_bench``."""
    base: dict[str, Any] = {
        "sub_action": None,
        "model": "stub-model",
        "bench_limit": 0,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_dispatch_terminal_bench_in_valid_benches() -> None:
    """``terminal-bench`` is accepted by the dispatcher constant."""
    from chimera.shrew.benchmarks.cli import VALID_BENCHES

    assert "terminal-bench" in VALID_BENCHES


def test_dispatch_terminal_bench_dataset_absent_returns_3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Missing dataset → exit 3 + setup hint on stderr (no longer 'not wired')."""
    from chimera.shrew.benchmarks.cli import dispatch_bench

    monkeypatch.setenv("CHIMERA_TERMINAL_BENCH_PATH", str(tmp_path))
    rc = dispatch_bench(_make_args(sub_action="terminal-bench"))
    assert rc == 3
    err = capsys.readouterr().err
    assert "tasks.json" in err
    assert "CHIMERA_TERMINAL_BENCH_PATH" in err
    # Critically, the legacy "not yet wired" surface is gone.
    assert "not yet wired" not in err


def test_dispatch_terminal_bench_runs_with_patched_agent(
    tb_dataset: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Round-trip: dataset present + mock agent + harness yields exit 0.

    The agent's output is ignored by the verify command. ``true``
    passes for tb-001; ``false`` fails for tb-002; the staged tb-003
    workdir succeeds because the dataset fixture pre-staged the file.
    """
    monkeypatch.setenv("CHIMERA_TERMINAL_BENCH_PATH", str(tb_dataset))

    fake_agent = MagicMock()
    fake_agent.run.return_value = MagicMock(output="ignored", cost=0.0, steps=1)

    with patch(
        "chimera.shrew.benchmarks.cli.build_shrew_agent_for_eval",
        return_value=fake_agent,
    ):
        from chimera.shrew.benchmarks.cli import dispatch_bench

        rc = dispatch_bench(
            _make_args(sub_action="terminal-bench", bench_limit=3)
        )

    out = capsys.readouterr().out
    assert rc == 0
    assert "terminal-bench" in out
    assert "passed=" in out


def test_dispatch_terminal_bench_zero_passes_returns_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """When all verify commands fail, dispatch_bench exits 1."""
    # Stage a one-task dataset whose verify always fails.
    tasks = [
        {
            "task_id": "tb-fail",
            "instruction": "Will not pass.",
            "verify_command": "false",
        }
    ]
    (tmp_path / "tasks.json").write_text(json.dumps(tasks))
    monkeypatch.setenv("CHIMERA_TERMINAL_BENCH_PATH", str(tmp_path))

    fake_agent = MagicMock()
    fake_agent.run.return_value = MagicMock(output="ignored", cost=0.0, steps=1)

    with patch(
        "chimera.shrew.benchmarks.cli.build_shrew_agent_for_eval",
        return_value=fake_agent,
    ):
        from chimera.shrew.benchmarks.cli import dispatch_bench

        rc = dispatch_bench(
            _make_args(sub_action="terminal-bench", bench_limit=1)
        )

    assert rc == 1
