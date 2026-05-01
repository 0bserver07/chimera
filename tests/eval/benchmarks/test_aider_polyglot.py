"""Tests for ``chimera.eval.benchmarks.aider_polyglot``.

Covers the general (multi-CLI) Aider Polyglot adapter:

* dataset loading from a synthetic 3-task / 2-language ``tasks.json``
* the ``languages=[...]`` multi-language filter and the
  back-compat ``language="..."`` single-language filter
* the diff-match scorer (env-aware + agent-output fallback)
* the test-command scorer (round-trip via ``true``/``false``)
* env-var override of the dataset root
* the setup-hint helper

All tests are stdlib-only and never touch the network or the user's
filesystem outside ``tmp_path``. The agent / harness is never run —
this is loader + scorer coverage only.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chimera.eval.benchmarks.aider_polyglot import (
    SUPPORTED_LANGUAGES,
    AiderPolyglot,
    dataset_available,
    default_dataset_path,
    setup_hint,
)
from chimera.eval.harness import Benchmark


# ---------------------------------------------------------------------------
# Shared fixture: synthetic 3-task / 2-language dataset.
# ---------------------------------------------------------------------------


@pytest.fixture
def polyglot_dataset(tmp_path: Path) -> Path:
    """Stage a tiny Aider Polyglot dataset under ``tmp_path``.

    Three tasks across two languages:

    * ``python/hello-world`` — diff-match (single file)
    * ``python/run-tests``   — test-command (``true``)
    * ``rust/leap``          — diff-match (nested file path)
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
    # Stage the run-tests exercise dir so the test-command branch has a cwd.
    (tmp_path / "exercises" / "run-tests").mkdir(parents=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Module-level helpers + ABC conformance.
# ---------------------------------------------------------------------------


def test_subclasses_benchmark() -> None:
    """The general adapter must implement the standard Benchmark ABC."""
    assert issubclass(AiderPolyglot, Benchmark)


def test_supported_languages_has_six_entries() -> None:
    """Aider Polyglot upstream covers exactly six languages."""
    assert len(SUPPORTED_LANGUAGES) == 6
    assert set(SUPPORTED_LANGUAGES) == {
        "python",
        "javascript",
        "rust",
        "go",
        "java",
        "cpp",
    }


def test_dataset_absent_returns_empty(tmp_path: Path) -> None:
    """No ``tasks.json`` → ``tasks()`` returns ``[]`` (no exception)."""
    bench = AiderPolyglot(dataset_path=str(tmp_path))
    assert dataset_available(tmp_path) is False
    assert bench.tasks() == []


def test_dataset_available_true(polyglot_dataset: Path) -> None:
    """Once ``tasks.json`` is staged, ``dataset_available`` is True."""
    assert dataset_available(polyglot_dataset) is True


def test_setup_hint_mentions_env_var_and_upstream(tmp_path: Path) -> None:
    """The setup hint surfaces the env-var override and upstream repo."""
    hint = setup_hint(tmp_path)
    assert "tasks.json" in hint
    assert "CHIMERA_AIDER_POLYGLOT_PATH" in hint
    assert "polyglot-benchmark" in hint


def test_env_var_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``CHIMERA_AIDER_POLYGLOT_PATH`` overrides the default root."""
    monkeypatch.setenv("CHIMERA_AIDER_POLYGLOT_PATH", str(tmp_path))
    assert default_dataset_path() == tmp_path


# ---------------------------------------------------------------------------
# Dataset loading + filtering.
# ---------------------------------------------------------------------------


def test_loads_all_tasks(polyglot_dataset: Path) -> None:
    """Without filters, every task is returned."""
    bench = AiderPolyglot(dataset_path=str(polyglot_dataset))
    tasks = bench.tasks()
    assert len(tasks) == 3
    assert {t["id"] for t in tasks} == {
        "python/hello-world",
        "python/run-tests",
        "rust/leap",
    }
    assert bench.name() == "aider-polyglot"


def test_languages_list_filter(polyglot_dataset: Path) -> None:
    """``languages=['python']`` keeps only the two python tasks."""
    bench = AiderPolyglot(
        dataset_path=str(polyglot_dataset), languages=["python"]
    )
    tasks = bench.tasks()
    assert len(tasks) == 2
    assert all(t["language"] == "python" for t in tasks)
    # Single-element list still suffixes with the bare language name.
    assert bench.name() == "aider-polyglot:python"


def test_languages_multi_filter(polyglot_dataset: Path) -> None:
    """A multi-language filter keeps tasks from any listed language."""
    bench = AiderPolyglot(
        dataset_path=str(polyglot_dataset), languages=["python", "rust"]
    )
    tasks = bench.tasks()
    assert len(tasks) == 3
    # Multi-language name is sorted-stable so it diff-cleanly across runs.
    assert bench.name() == "aider-polyglot:python+rust"


def test_languages_filter_excludes_other(polyglot_dataset: Path) -> None:
    """A filter that excludes every task yields an empty list."""
    bench = AiderPolyglot(dataset_path=str(polyglot_dataset), languages=["go"])
    assert bench.tasks() == []


def test_back_compat_language_kwarg(polyglot_dataset: Path) -> None:
    """The legacy single-language kwarg still filters correctly."""
    bench = AiderPolyglot(dataset_path=str(polyglot_dataset), language="rust")
    tasks = bench.tasks()
    assert len(tasks) == 1
    assert tasks[0]["id"] == "rust/leap"
    # The back-compat property mirrors the (single-element) filter.
    assert bench.language == "rust"


def test_languages_wins_over_language(polyglot_dataset: Path) -> None:
    """When both kwargs are supplied, ``languages`` takes precedence."""
    bench = AiderPolyglot(
        dataset_path=str(polyglot_dataset),
        languages=["rust"],
        language="python",  # ignored
    )
    tasks = bench.tasks()
    assert len(tasks) == 1
    assert tasks[0]["language"] == "rust"


def test_limit_applied_after_filter(polyglot_dataset: Path) -> None:
    """``limit`` caps the post-filter list."""
    bench = AiderPolyglot(
        dataset_path=str(polyglot_dataset), languages=["python"], limit=1
    )
    assert len(bench.tasks()) == 1


def test_limit_zero_is_no_cap(polyglot_dataset: Path) -> None:
    """``limit=0`` (non-positive) means no cap."""
    bench = AiderPolyglot(dataset_path=str(polyglot_dataset), limit=0)
    assert len(bench.tasks()) == 3


def test_malformed_json_returns_empty(tmp_path: Path) -> None:
    """Malformed ``tasks.json`` is treated as a missing dataset."""
    (tmp_path / "tasks.json").write_text("{not json")
    bench = AiderPolyglot(dataset_path=str(tmp_path))
    assert bench.tasks() == []


def test_wrapper_dict_shape(tmp_path: Path) -> None:
    """``{'tasks': [...]}`` wrapper form is supported alongside bare list."""
    payload = {
        "tasks": [
            {"id": "x", "language": "python", "prompt": "p"},
        ]
    }
    (tmp_path / "tasks.json").write_text(json.dumps(payload))
    bench = AiderPolyglot(dataset_path=str(tmp_path))
    assert len(bench.tasks()) == 1


def test_non_dict_entries_filtered_out(tmp_path: Path) -> None:
    """Non-dict entries in ``tasks.json`` are silently dropped."""
    payload = [
        {"id": "ok", "language": "python", "prompt": "p"},
        "garbage",
        42,
    ]
    (tmp_path / "tasks.json").write_text(json.dumps(payload))
    bench = AiderPolyglot(dataset_path=str(tmp_path))
    tasks = bench.tasks()
    assert len(tasks) == 1
    assert tasks[0]["id"] == "ok"


def test_tasks_cached(polyglot_dataset: Path) -> None:
    """``tasks()`` caches its result; the underlying file is read once."""
    bench = AiderPolyglot(dataset_path=str(polyglot_dataset))
    first = bench.tasks()
    second = bench.tasks()
    assert first is second  # Cached identity, not just equality.


# ---------------------------------------------------------------------------
# Diff-match scoring.
# ---------------------------------------------------------------------------


def test_diff_match_pass_via_agent_output(polyglot_dataset: Path) -> None:
    """When env is None, the agent's raw output is the actual content."""
    bench = AiderPolyglot(dataset_path=str(polyglot_dataset), languages=["rust"])
    task = bench.tasks()[0]
    assert bench.evaluate(task, "// gold\n", env=None) is True


def test_diff_match_fail_on_mismatch(polyglot_dataset: Path) -> None:
    """Diff-match fails when the agent output diverges from gold."""
    bench = AiderPolyglot(dataset_path=str(polyglot_dataset), languages=["rust"])
    task = bench.tasks()[0]
    assert bench.evaluate(task, "// wrong\n", env=None) is False


def test_diff_match_strips_fenced_code(polyglot_dataset: Path) -> None:
    """Fenced code blocks are stripped before comparison."""
    bench = AiderPolyglot(
        dataset_path=str(polyglot_dataset), languages=["python"]
    )
    task = next(t for t in bench.tasks() if t["id"] == "python/hello-world")
    fenced = (
        "Here you go:\n"
        "```python\n"
        "def hello():\n"
        "    return 'Hello, World!'\n"
        "```\n"
    )
    assert bench.evaluate(task, fenced, env=None) is True


def test_diff_match_uses_env_workdir(
    polyglot_dataset: Path, tmp_path: Path
) -> None:
    """When the env exposes a ``workdir``, the actual file is read from it."""
    bench = AiderPolyglot(dataset_path=str(polyglot_dataset), languages=["rust"])
    task = bench.tasks()[0]

    workdir = tmp_path / "wd"
    (workdir / "src").mkdir(parents=True)
    (workdir / "src" / "lib.rs").write_text("// gold\n")
    env = MagicMock(workdir=str(workdir), spec=["workdir"])
    # Ensure the env doesn't accidentally expose read_file (we want the
    # workdir branch, not the env-reader branch).
    del env.read_file

    assert bench.evaluate(task, "ignored", env=env) is True


def test_diff_match_uses_env_read_file(polyglot_dataset: Path) -> None:
    """An env exposing ``read_file`` is preferred over the workdir branch."""
    bench = AiderPolyglot(dataset_path=str(polyglot_dataset), languages=["rust"])
    task = bench.tasks()[0]

    env = MagicMock(spec=["read_file"])
    env.read_file.return_value = "// gold\n"
    assert bench.evaluate(task, "ignored", env=env) is True
    env.read_file.assert_called_once_with("src/lib.rs")


# ---------------------------------------------------------------------------
# Test-command scoring.
# ---------------------------------------------------------------------------


def test_test_command_pass(polyglot_dataset: Path) -> None:
    """``true`` exits 0 → the task passes."""
    bench = AiderPolyglot(
        dataset_path=str(polyglot_dataset), languages=["python"]
    )
    task = next(t for t in bench.tasks() if t["id"] == "python/run-tests")
    assert bench.evaluate(task, "ignored", env=None) is True


def test_test_command_fail(polyglot_dataset: Path) -> None:
    """``false`` exits 1 → the task fails."""
    bench = AiderPolyglot(
        dataset_path=str(polyglot_dataset), languages=["python"]
    )
    task = next(t for t in bench.tasks() if t["id"] == "python/run-tests")
    task = dict(task)
    task["test_command"] = "false"
    assert bench.evaluate(task, "ignored", env=None) is False


def test_underspecified_returns_false() -> None:
    """A task with neither expected_files nor test_command fails-closed."""
    bench = AiderPolyglot()
    assert bench.evaluate({"id": "x", "prompt": "p"}, "anything", env=None) is False
