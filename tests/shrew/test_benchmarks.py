"""Tests for ``chimera.shrew.benchmarks``.

Covers the three deliverables for agent S4:

* :mod:`chimera.shrew.benchmarks.aider_polyglot` — schema loading, the
  language filter, the limit slot, the diff-match scorer, and the
  ``test_command`` scorer (via a tiny shell-out fixture).
* :mod:`chimera.shrew.benchmarks.gaia` — schema loading, the level
  filter, the answer-extraction helper, the GAIA-style scorer
  (string / numeric / list paths), and the prompt-shim builder.
* :mod:`chimera.shrew.benchmarks.cli` — ``dispatch_bench`` exit codes
  for missing / unknown / malformed names, dataset-absent skip with
  setup hint, and round-trip success when the dataset and agent are
  patched in.

All tests are stdlib-only and never touch the network or the user's
filesystem outside ``tmp_path``. The agent is always patched so no
provider SDK / API key is required.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def aider_dataset(tmp_path: Path) -> Path:
    """Stage a tiny Aider Polyglot dataset under ``tmp_path``.

    Three tasks: a python diff-match task, a python test-pass task, and
    a rust diff-match task — covers the language filter and both
    grading modes.
    """
    tasks = [
        {
            "id": "python/hello-world",
            "language": "python",
            "prompt": "Implement hello().",
            "expected_files": {
                "hello.py": "def hello():\n    return 'Hello, World!'\n"
            },
        },
        {
            "id": "python/run-tests",
            "language": "python",
            "prompt": "Make the tests pass.",
            "test_command": "true",
            "exercise_dir": "run-tests",
            "timeout_s": 5,
        },
        {
            "id": "rust/leap",
            "language": "rust",
            "prompt": "Implement leap year.",
            "expected_files": {"src/lib.rs": "// gold\n"},
        },
    ]
    (tmp_path / "tasks.json").write_text(json.dumps(tasks))
    # Stage the run-tests exercise dir so test-command mode has a cwd.
    (tmp_path / "exercises" / "run-tests").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def gaia_dataset(tmp_path: Path) -> Path:
    """Stage a tiny GAIA dataset under ``tmp_path``.

    Three tasks across L1 / L2 to exercise the level filter, plus a
    numeric-gold and a list-gold to exercise the scorer branches.
    """
    tasks = [
        {
            "task_id": "abc-1",
            "Question": "Capital of France?",
            "Final answer": "Paris",
            "Level": 1,
        },
        {
            "task_id": "abc-2",
            "Question": "Sum of 2 and 2?",
            "Final answer": "4",
            "Level": 1,
        },
        {
            "task_id": "abc-3",
            "Question": "First three primes?",
            "Final answer": "2, 3, 5",
            "Level": 2,
        },
    ]
    (tmp_path / "tasks.json").write_text(json.dumps(tasks))
    return tmp_path


# ---------------------------------------------------------------------------
# Adapter import smoke tests
# ---------------------------------------------------------------------------


def test_adapters_import() -> None:
    """All three modules import without dragging in provider SDKs."""
    from chimera.shrew.benchmarks import aider_polyglot, cli, gaia

    assert aider_polyglot.AiderPolyglot is not None
    assert gaia.GAIA is not None
    assert cli.dispatch_bench is not None


def test_aider_polyglot_subclasses_benchmark() -> None:
    """The adapter implements the standard :class:`Benchmark` ABC."""
    from chimera.eval.harness import Benchmark
    from chimera.shrew.benchmarks.aider_polyglot import AiderPolyglot

    assert issubclass(AiderPolyglot, Benchmark)


def test_gaia_subclasses_benchmark() -> None:
    """The adapter implements the standard :class:`Benchmark` ABC."""
    from chimera.eval.harness import Benchmark
    from chimera.shrew.benchmarks.gaia import GAIA

    assert issubclass(GAIA, Benchmark)


# ---------------------------------------------------------------------------
# Aider Polyglot — dataset loading
# ---------------------------------------------------------------------------


def test_aider_dataset_absent_returns_empty(tmp_path: Path) -> None:
    """When ``tasks.json`` is missing, ``tasks()`` returns ``[]``."""
    from chimera.shrew.benchmarks.aider_polyglot import (
        AiderPolyglot,
        dataset_available,
    )

    bench = AiderPolyglot(dataset_path=str(tmp_path))
    assert dataset_available(tmp_path) is False
    assert bench.tasks() == []


def test_aider_dataset_available_true(aider_dataset: Path) -> None:
    """``dataset_available`` is ``True`` once ``tasks.json`` is staged."""
    from chimera.shrew.benchmarks.aider_polyglot import dataset_available

    assert dataset_available(aider_dataset) is True


def test_aider_loads_all_tasks(aider_dataset: Path) -> None:
    """Without a filter, every task in ``tasks.json`` is returned."""
    from chimera.shrew.benchmarks.aider_polyglot import AiderPolyglot

    bench = AiderPolyglot(dataset_path=str(aider_dataset))
    tasks = bench.tasks()
    assert len(tasks) == 3
    assert {t["id"] for t in tasks} == {
        "python/hello-world",
        "python/run-tests",
        "rust/leap",
    }


def test_aider_language_filter(aider_dataset: Path) -> None:
    """``language='python'`` keeps only the two python tasks."""
    from chimera.shrew.benchmarks.aider_polyglot import AiderPolyglot

    bench = AiderPolyglot(dataset_path=str(aider_dataset), language="python")
    tasks = bench.tasks()
    assert len(tasks) == 2
    assert all(t["language"] == "python" for t in tasks)
    assert bench.name() == "aider-polyglot:python"


def test_aider_limit(aider_dataset: Path) -> None:
    """``limit`` caps the number of tasks returned."""
    from chimera.shrew.benchmarks.aider_polyglot import AiderPolyglot

    bench = AiderPolyglot(dataset_path=str(aider_dataset), limit=1)
    assert len(bench.tasks()) == 1


def test_aider_setup_hint_mentions_env_var(tmp_path: Path) -> None:
    """The setup hint surfaces the env-var override and expected file."""
    from chimera.shrew.benchmarks.aider_polyglot import setup_hint

    hint = setup_hint(tmp_path)
    assert "tasks.json" in hint
    assert "CHIMERA_AIDER_POLYGLOT_PATH" in hint


def test_aider_env_var_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``CHIMERA_AIDER_POLYGLOT_PATH`` overrides the default dataset root."""
    from chimera.shrew.benchmarks.aider_polyglot import default_dataset_path

    monkeypatch.setenv("CHIMERA_AIDER_POLYGLOT_PATH", str(tmp_path))
    assert default_dataset_path() == tmp_path


# ---------------------------------------------------------------------------
# Aider Polyglot — scoring
# ---------------------------------------------------------------------------


def test_aider_diff_match_pass(aider_dataset: Path) -> None:
    """Diff-match passes when agent_output equals the gold contents."""
    from chimera.shrew.benchmarks.aider_polyglot import AiderPolyglot

    bench = AiderPolyglot(dataset_path=str(aider_dataset), language="rust")
    task = bench.tasks()[0]
    assert bench.evaluate(task, "// gold\n", env=None) is True


def test_aider_diff_match_fail(aider_dataset: Path) -> None:
    """Diff-match fails when agent_output diverges from gold."""
    from chimera.shrew.benchmarks.aider_polyglot import AiderPolyglot

    bench = AiderPolyglot(dataset_path=str(aider_dataset), language="rust")
    task = bench.tasks()[0]
    assert bench.evaluate(task, "// wrong\n", env=None) is False


def test_aider_diff_match_strips_fences(aider_dataset: Path) -> None:
    """Fenced code blocks have the fence stripped before comparison."""
    from chimera.shrew.benchmarks.aider_polyglot import AiderPolyglot

    bench = AiderPolyglot(dataset_path=str(aider_dataset), language="python")
    task = next(t for t in bench.tasks() if t["id"] == "python/hello-world")
    fenced = (
        "Sure thing!\n"
        "```python\n"
        "def hello():\n"
        "    return 'Hello, World!'\n"
        "```\n"
    )
    assert bench.evaluate(task, fenced, env=None) is True


def test_aider_diff_match_uses_env_workdir(
    aider_dataset: Path, tmp_path: Path
) -> None:
    """When the env exposes a workdir, the actual file is read from it."""
    from chimera.shrew.benchmarks.aider_polyglot import AiderPolyglot

    bench = AiderPolyglot(dataset_path=str(aider_dataset), language="rust")
    task = bench.tasks()[0]

    # Stage a "src/lib.rs" file matching gold inside a workdir env.
    workdir = tmp_path / "wd"
    (workdir / "src").mkdir(parents=True)
    (workdir / "src" / "lib.rs").write_text("// gold\n")
    env = MagicMock(workdir=str(workdir), spec=["workdir"])
    # ensure read_file is not callable so the workdir branch fires
    del env.read_file

    assert bench.evaluate(task, "ignored", env=env) is True


def test_aider_test_command_pass(aider_dataset: Path) -> None:
    """The ``true`` shell command makes the test-command branch pass."""
    from chimera.shrew.benchmarks.aider_polyglot import AiderPolyglot

    bench = AiderPolyglot(dataset_path=str(aider_dataset), language="python")
    task = next(t for t in bench.tasks() if t["id"] == "python/run-tests")
    assert bench.evaluate(task, "ignored", env=None) is True


def test_aider_test_command_fail(aider_dataset: Path) -> None:
    """The ``false`` shell command makes the test-command branch fail."""
    from chimera.shrew.benchmarks.aider_polyglot import AiderPolyglot

    bench = AiderPolyglot(dataset_path=str(aider_dataset), language="python")
    task = next(t for t in bench.tasks() if t["id"] == "python/run-tests")
    task = dict(task)
    task["test_command"] = "false"
    assert bench.evaluate(task, "ignored", env=None) is False


def test_aider_evaluate_underspecified_returns_false() -> None:
    """A task with neither expected_files nor test_command fails-closed."""
    from chimera.shrew.benchmarks.aider_polyglot import AiderPolyglot

    bench = AiderPolyglot()
    assert bench.evaluate({"id": "x", "prompt": "p"}, "anything", env=None) is False


# ---------------------------------------------------------------------------
# GAIA — dataset loading
# ---------------------------------------------------------------------------


def test_gaia_dataset_absent_returns_empty(tmp_path: Path) -> None:
    """Skip path: missing tasks.json yields empty list, not an error."""
    from chimera.shrew.benchmarks.gaia import GAIA, dataset_available

    bench = GAIA(dataset_path=str(tmp_path))
    assert dataset_available(tmp_path) is False
    assert bench.tasks() == []


def test_gaia_loads_all_tasks(gaia_dataset: Path) -> None:
    """Without a filter every task is returned, with synthesised prompt."""
    from chimera.shrew.benchmarks.gaia import GAIA

    bench = GAIA(dataset_path=str(gaia_dataset))
    tasks = bench.tasks()
    assert len(tasks) == 3
    assert {t["id"] for t in tasks} == {"abc-1", "abc-2", "abc-3"}
    # Every task carries the synthesised prompt.
    assert all("Answer:" in t["prompt"] for t in tasks)


def test_gaia_level_filter(gaia_dataset: Path) -> None:
    """``level=2`` keeps only the L2 task."""
    from chimera.shrew.benchmarks.gaia import GAIA

    bench = GAIA(dataset_path=str(gaia_dataset), level=2)
    tasks = bench.tasks()
    assert len(tasks) == 1
    assert tasks[0]["id"] == "abc-3"
    assert bench.name() == "gaia:level2"


def test_gaia_limit(gaia_dataset: Path) -> None:
    """``limit`` caps the number of tasks returned."""
    from chimera.shrew.benchmarks.gaia import GAIA

    bench = GAIA(dataset_path=str(gaia_dataset), limit=1)
    assert len(bench.tasks()) == 1


def test_gaia_setup_hint_mentions_env_var(tmp_path: Path) -> None:
    """The setup hint surfaces the env-var override and expected file."""
    from chimera.shrew.benchmarks.gaia import setup_hint

    hint = setup_hint(tmp_path)
    assert "tasks.json" in hint
    assert "CHIMERA_GAIA_PATH" in hint


def test_gaia_env_var_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``CHIMERA_GAIA_PATH`` overrides the default dataset root."""
    from chimera.shrew.benchmarks.gaia import default_dataset_path

    monkeypatch.setenv("CHIMERA_GAIA_PATH", str(tmp_path))
    assert default_dataset_path() == tmp_path


# ---------------------------------------------------------------------------
# GAIA — extraction + scoring helpers
# ---------------------------------------------------------------------------


def test_gaia_extract_final_answer_marker() -> None:
    """``Answer:`` line at the end is the canonical extraction."""
    from chimera.shrew.benchmarks.gaia import extract_final_answer

    text = "I think hard.\n\nAnswer: 42"
    assert extract_final_answer(text) == "42"


def test_gaia_extract_final_answer_strips_quotes() -> None:
    """Surrounding straight quotes are stripped from the answer."""
    from chimera.shrew.benchmarks.gaia import extract_final_answer

    assert extract_final_answer('Answer: "Paris"') == "Paris"
    assert extract_final_answer("Answer: 'Paris'") == "Paris"


def test_gaia_extract_final_answer_fallback() -> None:
    """When no marker is present, the last non-empty line is returned."""
    from chimera.shrew.benchmarks.gaia import extract_final_answer

    assert extract_final_answer("foo\nbar\n\n") == "bar"


def test_gaia_extract_final_answer_empty() -> None:
    """Empty / whitespace text yields the empty string."""
    from chimera.shrew.benchmarks.gaia import extract_final_answer

    assert extract_final_answer("") == ""
    assert extract_final_answer("   \n\n") == ""


def test_gaia_score_string_match() -> None:
    """Normalisation strips articles + accents + punctuation + case."""
    from chimera.shrew.benchmarks.gaia import score_answer

    ok, reason = score_answer("the Café", "cafe.")
    assert ok is True
    assert reason == "string-match"


def test_gaia_score_numeric_match() -> None:
    """Numeric gold accepts any numeric prediction within tolerance."""
    from chimera.shrew.benchmarks.gaia import score_answer

    ok, _ = score_answer("4", "4")
    assert ok is True
    ok, _ = score_answer("4.0", "4")
    assert ok is True
    ok, _ = score_answer("five", "4")
    assert ok is False


def test_gaia_score_list_match() -> None:
    """Comma-separated lists match by sorted-normalised set equality."""
    from chimera.shrew.benchmarks.gaia import score_answer

    ok, reason = score_answer("3, 5, 2", "2, 3, 5")
    assert ok is True
    assert reason == "list-match"


def test_gaia_score_empty_fail() -> None:
    """Empty predictions / gold yield a clear failure tag."""
    from chimera.shrew.benchmarks.gaia import score_answer

    ok, reason = score_answer("", "x")
    assert ok is False
    assert "empty" in reason


def test_gaia_evaluate_uses_extractor(gaia_dataset: Path) -> None:
    """``evaluate`` round-trips through extract + score."""
    from chimera.shrew.benchmarks.gaia import GAIA

    bench = GAIA(dataset_path=str(gaia_dataset))
    task = next(t for t in bench.tasks() if t["id"] == "abc-1")
    assert bench.evaluate(task, "Answer: Paris", env=None) is True
    assert bench.evaluate(task, "Answer: London", env=None) is False


def test_gaia_evaluate_no_gold_fails(tmp_path: Path) -> None:
    """Test-split tasks (no gold) evaluate to False, never True."""
    from chimera.shrew.benchmarks.gaia import GAIA

    bench = GAIA()
    task = {"task_id": "x", "Question": "?"}
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


def test_dispatch_missing_name_returns_2(capsys: pytest.CaptureFixture) -> None:
    """No benchmark name → exit 2 + stderr error."""
    from chimera.shrew.benchmarks.cli import dispatch_bench

    rc = dispatch_bench(_make_args())
    assert rc == 2
    err = capsys.readouterr().err
    assert "requires a benchmark name" in err


def test_dispatch_unknown_name_returns_2(capsys: pytest.CaptureFixture) -> None:
    """Unknown benchmark → exit 2 + stderr error."""
    from chimera.shrew.benchmarks.cli import dispatch_bench

    rc = dispatch_bench(_make_args(sub_action="bogus"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown benchmark" in err


def test_dispatch_terminal_bench_not_wired(
    capsys: pytest.CaptureFixture,
) -> None:
    """``terminal-bench`` is reserved but not wired in this scaffold."""
    from chimera.shrew.benchmarks.cli import dispatch_bench

    rc = dispatch_bench(_make_args(sub_action="terminal-bench"))
    assert rc == 3
    err = capsys.readouterr().err
    assert "not yet wired" in err


def test_dispatch_aider_dataset_absent_returns_3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Missing dataset → exit 3 + setup hint on stderr."""
    from chimera.shrew.benchmarks.cli import dispatch_bench

    monkeypatch.setenv("CHIMERA_AIDER_POLYGLOT_PATH", str(tmp_path))
    rc = dispatch_bench(_make_args(sub_action="aider-polyglot"))
    assert rc == 3
    err = capsys.readouterr().err
    assert "tasks.json" in err
    assert "CHIMERA_AIDER_POLYGLOT_PATH" in err


def test_dispatch_gaia_dataset_absent_returns_3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """GAIA: missing dataset → exit 3 + setup hint on stderr."""
    from chimera.shrew.benchmarks.cli import dispatch_bench

    monkeypatch.setenv("CHIMERA_GAIA_PATH", str(tmp_path))
    rc = dispatch_bench(_make_args(sub_action="gaia"))
    assert rc == 3
    err = capsys.readouterr().err
    assert "tasks.json" in err
    assert "CHIMERA_GAIA_PATH" in err


def test_dispatch_aider_runs_with_patched_agent(
    aider_dataset: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Round-trip: dataset present + mock agent + harness yields exit 0.

    We patch the agent factory at the cli module so we never touch a
    provider SDK. The agent's ``run`` returns the gold rust solution so
    one of the three tasks passes — enough for ``rc == 0``.
    """
    monkeypatch.setenv("CHIMERA_AIDER_POLYGLOT_PATH", str(aider_dataset))

    fake_agent = MagicMock()
    fake_agent.run.return_value = MagicMock(output="// gold\n", cost=0.0, steps=1)

    with patch(
        "chimera.shrew.benchmarks.cli.build_shrew_agent_for_eval",
        return_value=fake_agent,
    ):
        from chimera.shrew.benchmarks.cli import dispatch_bench

        rc = dispatch_bench(
            _make_args(sub_action="aider-polyglot", bench_limit=3)
        )

    out = capsys.readouterr().out
    assert rc == 0
    assert "aider-polyglot" in out
    assert "passed=" in out


def test_dispatch_gaia_runs_with_patched_agent(
    gaia_dataset: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Round-trip: GAIA dataset + mock agent + harness yields exit 0."""
    monkeypatch.setenv("CHIMERA_GAIA_PATH", str(gaia_dataset))

    fake_agent = MagicMock()
    fake_agent.run.return_value = MagicMock(
        output="Answer: Paris", cost=0.0, steps=1
    )

    with patch(
        "chimera.shrew.benchmarks.cli.build_shrew_agent_for_eval",
        return_value=fake_agent,
    ):
        from chimera.shrew.benchmarks.cli import dispatch_bench

        rc = dispatch_bench(_make_args(sub_action="gaia", bench_limit=3))

    out = capsys.readouterr().out
    # One of three answers ("Paris") matches; rate > 0 → rc 0.
    assert rc == 0
    assert "gaia" in out


def test_dispatch_gaia_runs_zero_passes_returns_1(
    gaia_dataset: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """When no task passes, dispatch_bench exits 1 (ran-but-empty)."""
    monkeypatch.setenv("CHIMERA_GAIA_PATH", str(gaia_dataset))

    fake_agent = MagicMock()
    fake_agent.run.return_value = MagicMock(
        output="Answer: NotEvenClose", cost=0.0, steps=1
    )

    with patch(
        "chimera.shrew.benchmarks.cli.build_shrew_agent_for_eval",
        return_value=fake_agent,
    ):
        from chimera.shrew.benchmarks.cli import dispatch_bench

        rc = dispatch_bench(_make_args(sub_action="gaia", bench_limit=3))

    assert rc == 1


def test_dispatch_valid_benches_constant() -> None:
    """``VALID_BENCHES`` exposes the pair S1's parser knows about."""
    from chimera.shrew.benchmarks.cli import VALID_BENCHES

    assert "aider-polyglot" in VALID_BENCHES
    assert "gaia" in VALID_BENCHES
