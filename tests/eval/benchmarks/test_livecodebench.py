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


# ---------------------------------------------------------------------------
# The functional (LeetCode) grading contract — T14.
#
# LiveCodeBench mixes two contracts and the task says which. Everything used to
# run through ``python solution.py < stdin``, which graded the functional half
# against a contract it cannot satisfy: those solutions define a class and print
# nothing, so they scored 0 however correct they were. 63 of 175 staged tasks —
# 36% of the denominator unpassable by construction.
#
# These use a REAL LocalEnvironment, not the fake: the whole point is that the
# generated driver actually imports the solution and calls the method. A fake
# env that returns a canned exit code would assert nothing about the contract.
# ---------------------------------------------------------------------------
from chimera.env.local import LocalEnvironment  # noqa: E402
from chimera.eval.benchmarks.livecodebench import (  # noqa: E402
    _entry_point,
    _is_functional,
    _stratified_head,
)

_STARTER = "class Solution:\n    def addTwo(self, a: List[int], b: int) -> List[int]:\n"

_FUNCTIONAL_TASK = {
    "id": "lc/1",
    "platform": "leetcode",
    "starter_code": _STARTER,
    "test_cases": [
        {"input": "[1, 2]\n10", "output": "[11, 12]", "testtype": "functional"},
        {"input": "[5]\n1", "output": "[6]", "testtype": "functional"},
    ],
}

_GOOD = (
    "class Solution:\n"
    "    def addTwo(self, a, b):\n"
    "        return [x + b for x in a]\n"
)


def _local(tmp_path):
    return LocalEnvironment(workdir=str(tmp_path))


class TestContractDetection:
    def test_functional_cases_are_detected(self) -> None:
        assert _is_functional(_FUNCTIONAL_TASK["test_cases"]) is True

    def test_stdin_cases_are_not_functional(self) -> None:
        assert _is_functional([{"input": "1", "output": "1", "testtype": "stdin"}]) is False

    def test_unlabelled_cases_default_to_stdin(self) -> None:
        # Never infer the contract from the shape of the answer.
        assert _is_functional([{"input": "1", "output": "1"}]) is False

    def test_entry_point_comes_from_starter_code(self) -> None:
        assert _entry_point(_STARTER) == "addTwo"

    def test_entry_point_absent_is_empty(self) -> None:
        assert _entry_point("") == ""
        assert _entry_point("def helper(x):\n    return x\n") == ""


class TestFunctionalGrading:
    def test_correct_solution_passes(self, tmp_path) -> None:
        # THE REGRESSION: this exact answer scored 0 before, because the runner
        # executed it as a script and compared its (empty) stdout.
        bench = LiveCodeBench()
        assert bench.evaluate(_FUNCTIONAL_TASK, _GOOD, _local(tmp_path)) is True

    def test_correct_solution_passes_when_fenced_with_prose(self, tmp_path) -> None:
        answer = f"Here you go:\n\n```python\n{_GOOD}```\n\nIt adds b to each element."
        bench = LiveCodeBench()
        assert bench.evaluate(_FUNCTIONAL_TASK, answer, _local(tmp_path)) is True

    def test_wrong_solution_fails(self, tmp_path) -> None:
        wrong = "class Solution:\n    def addTwo(self, a, b):\n        return a\n"
        bench = LiveCodeBench()
        assert bench.evaluate(_FUNCTIONAL_TASK, wrong, _local(tmp_path)) is False

    def test_solution_that_prints_the_answer_still_fails(self, tmp_path) -> None:
        # The inverse of the original bug: printing is not the contract here,
        # so a "stdout-shaped" answer must not sneak through.
        printer = "class Solution:\n    def addTwo(self, a, b):\n        print([11, 12])\n"
        bench = LiveCodeBench()
        assert bench.evaluate(_FUNCTIONAL_TASK, printer, _local(tmp_path)) is False

    def test_missing_solution_class_fails(self, tmp_path) -> None:
        bench = LiveCodeBench()
        assert bench.evaluate(
            _FUNCTIONAL_TASK, "def addTwo(a, b):\n    return a\n", _local(tmp_path)
        ) is False

    def test_wrong_method_name_fails(self, tmp_path) -> None:
        other = "class Solution:\n    def somethingElse(self, a, b):\n        return a\n"
        bench = LiveCodeBench()
        assert bench.evaluate(_FUNCTIONAL_TASK, other, _local(tmp_path)) is False

    def test_raising_solution_fails_rather_than_erroring_out(self, tmp_path) -> None:
        boom = "class Solution:\n    def addTwo(self, a, b):\n        raise ValueError('x')\n"
        bench = LiveCodeBench()
        assert bench.evaluate(_FUNCTIONAL_TASK, boom, _local(tmp_path)) is False

    def test_empty_and_prose_answers_fail(self, tmp_path) -> None:
        bench = LiveCodeBench()
        assert bench.evaluate(_FUNCTIONAL_TASK, "", _local(tmp_path)) is False
        assert bench.evaluate(_FUNCTIONAL_TASK, "I couldn't solve it.", _local(tmp_path)) is False

    def test_typing_annotations_in_the_answer_do_not_break_import(self, tmp_path) -> None:
        # LeetCode stubs annotate with bare List/Optional. Without the driver
        # injecting those names a CORRECT solution dies of NameError at import
        # and grades 0 — a fabricated zero of exactly the kind being fixed.
        annotated = (
            "class Solution:\n"
            "    def addTwo(self, a: List[int], b: int) -> List[int]:\n"
            "        return [x + b for x in a]\n"
        )
        bench = LiveCodeBench()
        assert bench.evaluate(_FUNCTIONAL_TASK, annotated, _local(tmp_path)) is True

    def test_formatting_differences_are_not_correctness(self, tmp_path) -> None:
        # Values are compared decoded, so "[1,4]" vs "[1, 4]" must agree.
        task = dict(_FUNCTIONAL_TASK, test_cases=[
            {"input": "[1, 2]\n10", "output": "[11,12]", "testtype": "functional"},
        ])
        bench = LiveCodeBench()
        assert bench.evaluate(task, _GOOD, _local(tmp_path)) is True


class TestStdinContractStillWorks:
    def test_stdin_task_grades_through_the_script_path(self, tmp_path) -> None:
        task = {
            "id": "ac/1",
            "platform": "atcoder",
            "test_cases": [{"input": "3\n", "output": "9", "testtype": "stdin"}],
        }
        good = "n = int(input())\nprint(n * n)\n"
        bench = LiveCodeBench()
        assert bench.evaluate(task, good, _local(tmp_path)) is True
        assert bench.evaluate(task, "print(0)\n", _local(tmp_path)) is False


class TestStratifiedSlicing:
    def _mixed(self):
        # Mirrors the staged file's layout: all AtCoder, THEN all LeetCode.
        return (
            [{"id": f"ac/{i}", "platform": "atcoder"} for i in range(112)]
            + [{"id": f"lc/{i}", "platform": "leetcode"} for i in range(63)]
        )

    def test_small_limit_is_no_longer_single_platform(self) -> None:
        # THE BUG: a contiguous head slice of a platform-blocked file made
        # `--limit 50` AtCoder-only while reporting itself as "livecodebench".
        picked = _stratified_head(self._mixed(), 50)
        platforms = {t["platform"] for t in picked}
        assert platforms == {"atcoder", "leetcode"}
        assert len(picked) == 50

    def test_split_is_balanced_while_both_groups_last(self) -> None:
        picked = _stratified_head(self._mixed(), 20)
        counts = {p: sum(1 for t in picked if t["platform"] == p)
                  for p in ("atcoder", "leetcode")}
        assert counts == {"atcoder": 10, "leetcode": 10}

    def test_limit_at_or_above_size_returns_everything(self) -> None:
        tasks = self._mixed()
        assert _stratified_head(tasks, len(tasks)) == tasks
        assert _stratified_head(tasks, 999) == tasks

    def test_single_platform_dataset_keeps_dataset_order(self) -> None:
        only = [{"id": f"ac/{i}", "platform": "atcoder"} for i in range(10)]
        assert _stratified_head(only, 3) == only[:3]

    def test_is_deterministic(self) -> None:
        # No RNG: a run must be reproducible from its arguments alone.
        tasks = self._mixed()
        assert _stratified_head(tasks, 37) == _stratified_head(tasks, 37)

    def test_exhausted_group_does_not_stall_the_fill(self) -> None:
        # 3 leetcode + 100 atcoder, limit 20: must still return 20.
        tasks = (
            [{"id": f"ac/{i}", "platform": "atcoder"} for i in range(100)]
            + [{"id": f"lc/{i}", "platform": "leetcode"} for i in range(3)]
        )
        picked = _stratified_head(tasks, 20)
        assert len(picked) == 20
        assert sum(1 for t in picked if t["platform"] == "leetcode") == 3
