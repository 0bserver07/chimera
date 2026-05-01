"""Tests for ``chimera.shrew.benchmarks.harbor`` (agent S3, wave 9).

Covers:

* :class:`HarborBench` schema loading + filters (category, difficulty,
  limit), prompt synthesis, and answer-extraction grading.
* The dataset-availability helpers and env-var override.
* The CLI dispatch path: missing dataset + setup hint, dataset-present
  round-trip with a mocked agent.

All tests are stdlib-only; the agent factory is patched at the cli
module so we never touch a provider SDK / API key.
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
def harbor_dataset(tmp_path: Path) -> Path:
    """Stage a tiny Harbor dataset under ``tmp_path``.

    Four tasks across two categories and two difficulty bands. Covers
    the string / numeric / list scoring branches.
    """
    tasks = [
        {
            "task_id": "harbor-001",
            "prompt": "Vessel A arrives at 14:00 and unloads in 30 minutes. When does unloading finish?",
            "answer": "14:30",
            "category": "scheduling",
            "difficulty": 1,
        },
        {
            "task_id": "harbor-002",
            "prompt": "Three crates weigh 100, 200, 300 kg. Total weight in kg?",
            "answer": "600",
            "category": "manifest",
            "difficulty": 1,
        },
        {
            "task_id": "harbor-003",
            "prompt": "Which berths are open: 1, 2, 3, or 4?",
            "answer": "1, 3",
            "category": "scheduling",
            "difficulty": 2,
        },
        {
            "task_id": "harbor-004",
            "prompt": "Container ID for the diesel shipment?",
            "final_answer": "MSCU1234567",
            "category": "manifest",
            "difficulty": 2,
        },
    ]
    (tmp_path / "tasks.json").write_text(json.dumps(tasks))
    return tmp_path


# ---------------------------------------------------------------------------
# Module / ABC smoke tests
# ---------------------------------------------------------------------------


def test_harbor_module_imports() -> None:
    """Module imports without dragging in provider SDKs."""
    from chimera.shrew.benchmarks import harbor

    assert harbor.HarborBench is not None
    assert harbor.dataset_available is not None
    assert harbor.setup_hint is not None


def test_harbor_subclasses_benchmark() -> None:
    """The adapter implements the standard :class:`Benchmark` ABC."""
    from chimera.eval.harness import Benchmark
    from chimera.shrew.benchmarks.harbor import HarborBench

    assert issubclass(HarborBench, Benchmark)


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------


def test_harbor_dataset_absent_returns_empty(tmp_path: Path) -> None:
    """When ``tasks.json`` is missing, ``tasks()`` returns ``[]``."""
    from chimera.shrew.benchmarks.harbor import HarborBench, dataset_available

    bench = HarborBench(dataset_path=str(tmp_path))
    assert dataset_available(tmp_path) is False
    assert bench.tasks() == []


def test_harbor_dataset_available_true(harbor_dataset: Path) -> None:
    """``dataset_available`` is ``True`` once ``tasks.json`` is staged."""
    from chimera.shrew.benchmarks.harbor import dataset_available

    assert dataset_available(harbor_dataset) is True


def test_harbor_loads_all_tasks(harbor_dataset: Path) -> None:
    """Without filters, every task is returned with synthesised prompt."""
    from chimera.shrew.benchmarks.harbor import HarborBench

    bench = HarborBench(dataset_path=str(harbor_dataset))
    tasks = bench.tasks()
    assert len(tasks) == 4
    assert {t["id"] for t in tasks} == {
        "harbor-001",
        "harbor-002",
        "harbor-003",
        "harbor-004",
    }
    assert all("Answer:" in t["prompt"] for t in tasks)


def test_harbor_category_filter(harbor_dataset: Path) -> None:
    """``category='scheduling'`` keeps only the two scheduling tasks."""
    from chimera.shrew.benchmarks.harbor import HarborBench

    bench = HarborBench(dataset_path=str(harbor_dataset), category="scheduling")
    tasks = bench.tasks()
    assert len(tasks) == 2
    assert {t["id"] for t in tasks} == {"harbor-001", "harbor-003"}
    assert bench.name() == "harbor:scheduling"


def test_harbor_difficulty_filter(harbor_dataset: Path) -> None:
    """``difficulty=2`` keeps only the two L2 tasks."""
    from chimera.shrew.benchmarks.harbor import HarborBench

    bench = HarborBench(dataset_path=str(harbor_dataset), difficulty=2)
    tasks = bench.tasks()
    assert len(tasks) == 2
    assert {t["id"] for t in tasks} == {"harbor-003", "harbor-004"}
    assert bench.name() == "harbor:d2"


def test_harbor_combined_filters(harbor_dataset: Path) -> None:
    """``category`` + ``difficulty`` compose."""
    from chimera.shrew.benchmarks.harbor import HarborBench

    bench = HarborBench(
        dataset_path=str(harbor_dataset),
        category="scheduling",
        difficulty=1,
    )
    tasks = bench.tasks()
    assert len(tasks) == 1
    assert tasks[0]["id"] == "harbor-001"
    # Both filter suffixes appear in the name.
    name = bench.name()
    assert "scheduling" in name and "d1" in name


def test_harbor_limit(harbor_dataset: Path) -> None:
    """``limit`` caps the number of tasks returned."""
    from chimera.shrew.benchmarks.harbor import HarborBench

    bench = HarborBench(dataset_path=str(harbor_dataset), limit=2)
    assert len(bench.tasks()) == 2


def test_harbor_setup_hint_mentions_env_var(tmp_path: Path) -> None:
    """The setup hint surfaces the env-var override and expected file."""
    from chimera.shrew.benchmarks.harbor import setup_hint

    hint = setup_hint(tmp_path)
    assert "tasks.json" in hint
    assert "CHIMERA_HARBOR_PATH" in hint


def test_harbor_env_var_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``CHIMERA_HARBOR_PATH`` overrides the default dataset root."""
    from chimera.shrew.benchmarks.harbor import default_dataset_path

    monkeypatch.setenv("CHIMERA_HARBOR_PATH", str(tmp_path))
    assert default_dataset_path() == tmp_path


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def test_harbor_evaluate_string_match(harbor_dataset: Path) -> None:
    """String gold accepts case / punctuation variants via GAIA scorer."""
    from chimera.shrew.benchmarks.harbor import HarborBench

    bench = HarborBench(dataset_path=str(harbor_dataset))
    task = next(t for t in bench.tasks() if t["id"] == "harbor-001")
    assert bench.evaluate(task, "Answer: 14:30", env=None) is True
    assert bench.evaluate(task, "Answer: 15:00", env=None) is False


def test_harbor_evaluate_numeric_match(harbor_dataset: Path) -> None:
    """Numeric gold accepts ``600`` / ``600.0`` equally."""
    from chimera.shrew.benchmarks.harbor import HarborBench

    bench = HarborBench(dataset_path=str(harbor_dataset))
    task = next(t for t in bench.tasks() if t["id"] == "harbor-002")
    assert bench.evaluate(task, "Answer: 600", env=None) is True
    assert bench.evaluate(task, "Answer: 600.0", env=None) is True
    assert bench.evaluate(task, "Answer: six hundred", env=None) is False


def test_harbor_evaluate_list_match(harbor_dataset: Path) -> None:
    """Comma-separated lists match by sorted-normalised set equality."""
    from chimera.shrew.benchmarks.harbor import HarborBench

    bench = HarborBench(dataset_path=str(harbor_dataset))
    task = next(t for t in bench.tasks() if t["id"] == "harbor-003")
    # Gold "1, 3" — agent emits in different order.
    assert bench.evaluate(task, "Answer: 3, 1", env=None) is True


def test_harbor_evaluate_alt_gold_key(harbor_dataset: Path) -> None:
    """``final_answer`` is accepted as a fallback gold key."""
    from chimera.shrew.benchmarks.harbor import HarborBench

    bench = HarborBench(dataset_path=str(harbor_dataset))
    task = next(t for t in bench.tasks() if t["id"] == "harbor-004")
    assert bench.evaluate(task, "Answer: MSCU1234567", env=None) is True


def test_harbor_evaluate_no_gold_fails() -> None:
    """A task with no gold annotation evaluates to ``False``."""
    from chimera.shrew.benchmarks.harbor import HarborBench

    bench = HarborBench()
    task = {"task_id": "x", "prompt": "?"}
    assert bench.evaluate(task, "Answer: anything", env=None) is False


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


def test_dispatch_harbor_in_valid_benches() -> None:
    """``harbor`` joins the constant tuple of supported benchmark names."""
    from chimera.shrew.benchmarks.cli import VALID_BENCHES

    assert "harbor" in VALID_BENCHES


def test_dispatch_harbor_dataset_absent_returns_3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Missing dataset → exit 3 + setup hint on stderr."""
    from chimera.shrew.benchmarks.cli import dispatch_bench

    monkeypatch.setenv("CHIMERA_HARBOR_PATH", str(tmp_path))
    rc = dispatch_bench(_make_args(sub_action="harbor"))
    assert rc == 3
    err = capsys.readouterr().err
    assert "tasks.json" in err
    assert "CHIMERA_HARBOR_PATH" in err


def test_dispatch_harbor_runs_with_patched_agent(
    harbor_dataset: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Round-trip: dataset present + mock agent + harness yields exit 0.

    The agent emits ``Answer: 14:30`` for every task; only the
    matching scheduling task passes — enough for ``rc == 0``.
    """
    monkeypatch.setenv("CHIMERA_HARBOR_PATH", str(harbor_dataset))

    fake_agent = MagicMock()
    fake_agent.run.return_value = MagicMock(
        output="Answer: 14:30", cost=0.0, steps=1
    )

    with patch(
        "chimera.shrew.benchmarks.cli.build_shrew_agent_for_eval",
        return_value=fake_agent,
    ):
        from chimera.shrew.benchmarks.cli import dispatch_bench

        rc = dispatch_bench(_make_args(sub_action="harbor", bench_limit=4))

    out = capsys.readouterr().out
    assert rc == 0
    assert "harbor" in out
    assert "passed=" in out


def test_dispatch_harbor_zero_passes_returns_1(
    harbor_dataset: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """When no task passes, dispatch_bench exits 1 (ran-but-empty)."""
    monkeypatch.setenv("CHIMERA_HARBOR_PATH", str(harbor_dataset))

    fake_agent = MagicMock()
    fake_agent.run.return_value = MagicMock(
        output="Answer: NotEvenClose", cost=0.0, steps=1
    )

    with patch(
        "chimera.shrew.benchmarks.cli.build_shrew_agent_for_eval",
        return_value=fake_agent,
    ):
        from chimera.shrew.benchmarks.cli import dispatch_bench

        rc = dispatch_bench(_make_args(sub_action="harbor", bench_limit=4))

    assert rc == 1
