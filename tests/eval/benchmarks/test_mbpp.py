"""Tests for ``chimera/eval/benchmarks/mbpp.py``.

Covers:

* :class:`MBPP` task loading from JSON / JSONL / envelope formats.
* :meth:`MBPP.evaluate` with both the legacy ``test_setup_code`` and the
  sanitized split's ``test_imports`` shapes.
* :func:`default_dataset_path` precedence (env var > sanitized > legacy).
* :func:`dataset_available` truth/falsity.
* :func:`_cli` happy path + missing-dataset path.

Tests stay zero-dependency: no LLM, no provider SDK; the CLI test patches
``run_mbpp`` so no network call is issued.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from chimera.eval.benchmarks import mbpp as mbpp_mod
from chimera.eval.benchmarks.mbpp import (
    MBPP,
    SETUP_HINT,
    dataset_available,
    default_dataset_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitized_record() -> dict:
    """Build one record matching the sanitized-mbpp.json shape."""
    return {
        "task_id": 2,
        "source_file": "Benchmark Questions Verification V2.ipynb",
        "prompt": "Write a function to find the shared elements from the given two lists.",
        "code": (
            "def similar_elements(test_tup1, test_tup2):\n"
            "  res = tuple(set(test_tup1) & set(test_tup2))\n"
            "  return (res) "
        ),
        "test_imports": [],
        "test_list": [
            "assert set(similar_elements((3, 4, 5, 6),(5, 7, 4, 10))) == set((4, 5))",
            "assert set(similar_elements((1, 2, 3, 4),(5, 4, 3, 7))) == set((3, 4))",
        ],
    }


def _legacy_record() -> dict:
    """Build one record matching the original mbpp.jsonl shape."""
    return {
        "task_id": 7,
        "text": "Write a function that returns the square of n.",
        "code": "def sq(n):\n  return n * n",
        "test_setup_code": "",
        "test_list": [
            "assert sq(2) == 4",
            "assert sq(3) == 9",
        ],
    }


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_tasks_empty_when_no_dataset_path() -> None:
    bench = MBPP()
    assert bench.tasks() == []


def test_tasks_load_from_json_array(tmp_path: Path) -> None:
    path = tmp_path / "mbpp.json"
    path.write_text(json.dumps([_sanitized_record()]))
    bench = MBPP(dataset_path=str(path))
    tasks = bench.tasks()
    assert len(tasks) == 1
    assert tasks[0]["id"] == "Mbpp/2"
    assert tasks[0]["prompt"].startswith("Write a function to find the shared")
    # Original keys remain accessible.
    assert "test_list" in tasks[0]
    assert "code" in tasks[0]


def test_tasks_load_from_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "mbpp.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in [_legacy_record(), _sanitized_record()]))
    bench = MBPP(dataset_path=str(path))
    tasks = bench.tasks()
    assert len(tasks) == 2
    assert tasks[0]["id"] == "Mbpp/7"
    # Legacy 'text' is mapped to 'prompt'.
    assert tasks[0]["prompt"].startswith("Write a function that returns the square")


def test_tasks_load_from_envelope(tmp_path: Path) -> None:
    path = tmp_path / "mbpp.json"
    path.write_text(json.dumps({"tasks": [_sanitized_record()]}))
    bench = MBPP(dataset_path=str(path))
    assert len(bench.tasks()) == 1


def test_tasks_respects_limit(tmp_path: Path) -> None:
    path = tmp_path / "mbpp.json"
    records = [_sanitized_record() for _ in range(5)]
    for i, r in enumerate(records):
        r["task_id"] = i
    path.write_text(json.dumps(records))
    bench = MBPP(dataset_path=str(path), limit=2)
    assert len(bench.tasks()) == 2


def test_name_includes_split() -> None:
    bench = MBPP(split="sanitized")
    assert bench.name() == "mbpp-sanitized"
    bench = MBPP(split="test")
    assert bench.name() == "mbpp-test"


# ---------------------------------------------------------------------------
# Evaluate
# ---------------------------------------------------------------------------


def test_evaluate_canonical_solution_passes() -> None:
    bench = MBPP()
    record = _sanitized_record()
    record = MBPP._normalize(record)
    assert bench.evaluate(record, record["code"], None) is True


def test_evaluate_legacy_setup_code_runs() -> None:
    bench = MBPP()
    record = _legacy_record()
    record = MBPP._normalize(record)
    assert bench.evaluate(record, record["code"], None) is True


def test_evaluate_test_imports_are_executed() -> None:
    bench = MBPP()
    record = {
        "task_id": 82,
        "prompt": "Write a function that returns sqrt(n) rounded.",
        "test_imports": ["import math"],
        "test_list": ["assert isclose(math.sqrt(4), 2.0)"],
    }
    # Helper that uses ``isclose`` (math import alone won't bring it in)
    output = (
        "import math\n"
        "from math import isclose\n"
        "def helper():\n  return None\n"
    )
    record = MBPP._normalize(record)
    assert bench.evaluate(record, output, None) is True


def test_evaluate_returns_false_when_assertion_fails() -> None:
    bench = MBPP()
    record = MBPP._normalize(_sanitized_record())
    bad_output = "def similar_elements(a, b):\n  return ()"
    assert bench.evaluate(record, bad_output, None) is False


def test_evaluate_returns_false_when_test_list_missing() -> None:
    bench = MBPP()
    assert bench.evaluate({"id": "x", "prompt": ""}, "def f(): pass", None) is False


def test_evaluate_routes_to_env_when_provided() -> None:
    bench = MBPP()
    record = MBPP._normalize(_sanitized_record())
    env = MagicMock()
    env.run_command.return_value = MagicMock(exit_code=0)
    result = bench.evaluate(record, record["code"], env)
    env.write_file.assert_called_once()
    args = env.write_file.call_args
    assert args[0][0] == "solution.py"
    assert "similar_elements" in args[0][1]
    env.run_command.assert_called_once_with("python solution.py")
    assert result is True


def test_evaluate_env_nonzero_exit_is_failure() -> None:
    bench = MBPP()
    record = MBPP._normalize(_sanitized_record())
    env = MagicMock()
    env.run_command.return_value = MagicMock(exit_code=1)
    assert bench.evaluate(record, record["code"], env) is False


def test_evaluate_empty_output_fails() -> None:
    """An errored/empty agent run has no candidate function — never a pass."""
    bench = MBPP()
    record = MBPP._normalize(_sanitized_record())
    assert bench.evaluate(record, "", None) is False


def test_evaluate_whitespace_output_fails() -> None:
    bench = MBPP()
    record = MBPP._normalize(_sanitized_record())
    assert bench.evaluate(record, "   \n\t ", None) is False


def test_evaluate_empty_output_never_touches_env() -> None:
    """Empty output short-circuits before writing/executing solution.py."""
    bench = MBPP()
    record = MBPP._normalize(_sanitized_record())
    env = MagicMock()
    assert bench.evaluate(record, "", env) is False
    env.write_file.assert_not_called()
    env.run_command.assert_not_called()


# ---------------------------------------------------------------------------
# default_dataset_path / dataset_available
# ---------------------------------------------------------------------------


def test_default_dataset_path_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    target.write_text("[]")
    monkeypatch.setenv("CHIMERA_MBPP_PATH", str(target))
    assert default_dataset_path() == target
    assert dataset_available() is True


def test_default_dataset_path_falls_back_to_jsonl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CHIMERA_MBPP_PATH", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    # Neither sanitized nor legacy file exists.
    p = default_dataset_path()
    assert p.name == "mbpp.jsonl"
    assert dataset_available(p) is False


def test_default_dataset_path_prefers_sanitized(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CHIMERA_MBPP_PATH", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    base = tmp_path / ".chimera" / "datasets" / "mbpp"
    base.mkdir(parents=True)
    sanitized = base / "sanitized-mbpp.json"
    sanitized.write_text("[]")
    assert default_dataset_path() == sanitized


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_missing_dataset_returns_3(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    monkeypatch.delenv("CHIMERA_MBPP_PATH", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    rc = mbpp_mod._cli(["--limit", "1"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "MBPP dataset not staged" in err


def test_cli_happy_path_calls_run_mbpp(tmp_path: Path, capsys) -> None:
    """CLI should resolve the dataset, call run_mbpp, and exit 0."""
    path = tmp_path / "mbpp.json"
    path.write_text(json.dumps([_sanitized_record()]))

    from chimera.eval.harness import EvalResult, TaskEvalResult

    fake_result = EvalResult(
        benchmark="mbpp-sanitized",
        total=1,
        passed=1,
        pass_rate=1.0,
        results=[
            TaskEvalResult(task_id="Mbpp/2", passed=True, output="ok", cost=0.0, steps=1)
        ],
        total_cost=0.0,
    )
    with patch(
        "chimera.otter.benchmarks.run_mbpp", return_value=fake_result
    ) as run_mbpp:
        rc = mbpp_mod._cli(["--limit", "1", "--dataset-path", str(path)])
    assert rc == 0
    run_mbpp.assert_called_once()
    out = capsys.readouterr().out
    assert "mbpp-sanitized" in out
    assert "passed=1/1" in out


def test_setup_hint_mentions_curl_and_env_var() -> None:
    assert "curl" in SETUP_HINT
    assert "CHIMERA_MBPP_PATH" in SETUP_HINT
    assert "CC-BY-4.0" in SETUP_HINT
