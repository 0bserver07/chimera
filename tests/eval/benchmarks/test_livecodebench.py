"""Tests for the LiveCodeBench benchmark adapter.

Covers ctor ``scenario`` / ``difficulty`` validation, the ``name`` string
(with and without a date window), absent-dataset behavior, loading (JSON list
/ ``{"problems": [...]}``), the ``difficulty`` and date-``window`` filters,
``limit``, the ``rotated_window`` helper, ``DateWindow.contains``, and the
``evaluate`` contract: it grades the ``codegeneration`` scenario via stdin/
stdout test cases and raises ``NotImplementedError`` for the other (unwired)
scenarios.

No network, no Docker: a duck-typed fake env drives the codegeneration path.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from chimera.eval.benchmarks.livecodebench import DateWindow, LiveCodeBench


class _StdioEnv:
    """Env that echoes a fixed stdout for each ``python solution.py`` run.

    Mirrors the REAL Environment contract: ``run_command(cmd, ...)`` takes no
    ``stdin=`` kwarg — the adapter feeds stdin by writing ``_stdin.txt`` and
    shell-redirecting, so this fake records the last-written stdin file per run.
    """

    def __init__(self, *, exit_code: int = 0, stdout: str = "") -> None:
        self._exit_code = exit_code
        self._stdout = stdout
        self._files: dict[str, str] = {}
        self.runs: list[str | None] = []

    def write_file(self, path: str, content: str) -> None:
        self._files[path] = content

    def run_command(self, cmd: str, timeout: int | None = None, shell_name: str = "main"):
        self.runs.append(self._files.get("_stdin.txt"))
        return SimpleNamespace(exit_code=self._exit_code, stdout=self._stdout)


# --------------------------------------------------------------------------- ctor


def test_bad_scenario_raises() -> None:
    with pytest.raises(ValueError, match="scenario must be"):
        LiveCodeBench(scenario="quantum")


def test_bad_difficulty_raises() -> None:
    with pytest.raises(ValueError, match="difficulty must be"):
        LiveCodeBench(difficulty="trivial")


def test_name_default_and_with_window() -> None:
    assert LiveCodeBench().name() == "livecodebench-codegeneration"
    windowed = LiveCodeBench(start_date="2025-01-01", end_date="2025-03-01")
    assert windowed.name() == "livecodebench-codegeneration-2025-01-01_2025-03-01"


# ------------------------------------------------------------------- window logic


def test_date_window_contains() -> None:
    win = DateWindow(start=date(2025, 1, 1), end=date(2025, 12, 31))
    assert win.contains(date(2025, 6, 1)) is True
    assert win.contains(date(2024, 12, 31)) is False


def test_start_after_end_raises() -> None:
    with pytest.raises(ValueError, match="is after end_date"):
        LiveCodeBench(start_date="2025-06-01", end_date="2025-01-01")


def test_rotated_window_spans_cutoff_plus_months() -> None:
    bench = LiveCodeBench.rotated_window(model_cutoff="2025-01-01", months=2)
    end = (date(2025, 1, 1) + timedelta(days=60)).isoformat()
    assert bench.name() == f"livecodebench-codegeneration-2025-01-01_{end}"


# ------------------------------------------------------------------------- loading


def test_no_dataset_yields_no_tasks() -> None:
    assert LiveCodeBench().tasks() == []


def test_load_json_list(tmp_path: Path) -> None:
    path = tmp_path / "lcb.json"
    path.write_text(json.dumps([
        {"id": "p1", "difficulty": "easy"},
        {"id": "p2", "difficulty": "hard"},
    ]))
    assert len(LiveCodeBench(dataset_path=str(path)).tasks()) == 2


def test_load_problems_envelope(tmp_path: Path) -> None:
    path = tmp_path / "lcb.json"
    path.write_text(json.dumps({"problems": [{"id": "p1"}]}))
    assert len(LiveCodeBench(dataset_path=str(path)).tasks()) == 1


def test_difficulty_filter(tmp_path: Path) -> None:
    path = tmp_path / "lcb.json"
    path.write_text(json.dumps([
        {"id": "p1", "difficulty": "easy"},
        {"id": "p2", "difficulty": "hard"},
    ]))
    tasks = LiveCodeBench(dataset_path=str(path), difficulty="easy").tasks()
    assert [t["id"] for t in tasks] == ["p1"]


def test_window_filter_keeps_in_range_and_drops_undated(tmp_path: Path) -> None:
    path = tmp_path / "lcb.json"
    path.write_text(json.dumps([
        {"id": "in", "contest_date": "2025-02-15T00:00:00"},
        {"id": "out", "contest_date": "2024-11-01T00:00:00"},
        {"id": "undated"},
    ]))
    bench = LiveCodeBench(
        dataset_path=str(path), start_date="2025-01-01", end_date="2025-03-01"
    )
    assert [t["id"] for t in bench.tasks()] == ["in"]


def test_limit_respected(tmp_path: Path) -> None:
    path = tmp_path / "lcb.json"
    path.write_text(json.dumps([{"id": f"p{i}"} for i in range(5)]))
    assert len(LiveCodeBench(dataset_path=str(path), limit=2).tasks()) == 2


# ------------------------------------------------------------------------ evaluate


def test_evaluate_noncodegen_scenario_raises() -> None:
    """Honest-stub behavior: only 'codegeneration' is wired."""
    bench = LiveCodeBench(scenario="selfrepair")
    with pytest.raises(NotImplementedError, match="not yet wired"):
        bench.evaluate({"id": "p"}, "print(1)", _StdioEnv())


def test_evaluate_codegen_none_env_is_false() -> None:
    assert LiveCodeBench().evaluate({"test_cases": [{"input": "", "output": "1"}]},
                                    "print(1)", None) is False


def test_evaluate_codegen_missing_test_cases_returns_false() -> None:
    assert LiveCodeBench().evaluate({"id": "p"}, "print(1)", _StdioEnv(stdout="1")) is False


def test_evaluate_codegen_passes_when_output_matches() -> None:
    task = {"test_cases": [{"input": "", "output": "42"}]}
    env = _StdioEnv(exit_code=0, stdout="42\n")
    assert LiveCodeBench().evaluate(task, "print(42)", env) is True


def test_evaluate_codegen_fails_on_output_mismatch() -> None:
    task = {"test_cases": [{"input": "", "output": "42"}]}
    env = _StdioEnv(exit_code=0, stdout="99")
    assert LiveCodeBench().evaluate(task, "print(99)", env) is False


def test_evaluate_codegen_fails_on_nonzero_exit() -> None:
    task = {"public_test_cases": [{"input": "", "output": "42"}]}
    env = _StdioEnv(exit_code=1, stdout="42")
    assert LiveCodeBench().evaluate(task, "raise SystemExit(1)", env) is False


def test_evaluate_empty_output_fails() -> None:
    """An errored/empty agent run has no program to execute — never a pass."""
    task = {"test_cases": [{"input": "", "output": "42"}]}
    env = _StdioEnv(exit_code=0, stdout="")
    assert LiveCodeBench().evaluate(task, "", env) is False


def test_evaluate_empty_output_fails_even_when_expected_empty() -> None:
    """The dangerous case: an empty expected output means an empty program's
    empty stdout would spuriously MATCH without the guard."""
    task = {"test_cases": [{"input": "", "output": ""}]}
    env = _StdioEnv(exit_code=0, stdout="")
    assert LiveCodeBench().evaluate(task, "   \n  ", env) is False
    # Guard fires before writing/executing anything.
    assert env.runs == []
