"""Tests for the Context-Bench (Letta) benchmark adapter.

Covers ctor ``suite`` validation, the ``name`` string, graceful degradation
when the optional upstream ``leaderboard`` package is unavailable, loading
from a local JSON dataset (list and ``{"tasks": [...]}`` shapes) with
``dataset_size`` truncation, and the ``evaluate`` grading modes (exact
case-insensitive substring match and the callable-``judge`` override).

No network, no LLM: the upstream import is forced to fail via ``sys.modules``
so the offline fallback is exercised deterministically.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from chimera.eval.benchmarks.context_bench import ContextBench


# --------------------------------------------------------------------------- ctor


def test_default_suite_is_filesystem() -> None:
    assert ContextBench().suite == "filesystem"


def test_bad_suite_raises() -> None:
    with pytest.raises(ValueError, match="suite must be"):
        ContextBench(suite="nonsense")


def test_name_includes_suite() -> None:
    assert ContextBench(suite="filesystem").name() == "context-bench-filesystem"
    assert ContextBench(suite="skills").name() == "context-bench-skills"


# ------------------------------------------------------------------------- loading


def test_absent_dataset_and_no_upstream_yields_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No local dataset + no ``leaderboard`` package -> [] (no raise)."""
    monkeypatch.setitem(sys.modules, "leaderboard", None)
    assert ContextBench().tasks() == []


def test_load_json_list(tmp_path: Path) -> None:
    path = tmp_path / "ctx.json"
    path.write_text(json.dumps([
        {"id": "q1", "prompt": "Who owns Rex?", "answer": "Alice"},
        {"id": "q2", "prompt": "Where is Bob?", "answer": "Berlin"},
    ]))
    tasks = ContextBench(dataset_path=str(path)).tasks()
    assert len(tasks) == 2
    assert tasks[0]["answer"] == "Alice"


def test_load_envelope(tmp_path: Path) -> None:
    path = tmp_path / "ctx.json"
    path.write_text(json.dumps({"tasks": [{"id": "q1", "prompt": "p", "answer": "a"}]}))
    assert len(ContextBench(dataset_path=str(path)).tasks()) == 1


def test_dataset_size_truncates(tmp_path: Path) -> None:
    path = tmp_path / "ctx.json"
    path.write_text(json.dumps([
        {"id": f"q{i}", "prompt": "p", "answer": "a"} for i in range(4)
    ]))
    assert len(ContextBench(dataset_path=str(path), dataset_size=2).tasks()) == 2


# ------------------------------------------------------------------------ evaluate


def test_evaluate_exact_substring_case_insensitive() -> None:
    bench = ContextBench()
    task = {"id": "q", "prompt": "p", "answer": "Berlin"}
    assert bench.evaluate(task, "The answer is BERLIN.", env=None) is True
    assert bench.evaluate(task, "I think it's Paris.", env=None) is False


def test_evaluate_empty_truth_is_false() -> None:
    bench = ContextBench()
    assert bench.evaluate({"id": "q", "answer": ""}, "anything", env=None) is False


def test_evaluate_callable_judge_takes_precedence() -> None:
    calls: list[tuple[dict, str]] = []

    def judge(task: dict, output: str) -> bool:
        calls.append((task, output))
        return output == "correct"

    task = {"id": "q", "answer": "ignored-when-judge-present", "judge": judge}
    bench = ContextBench()
    assert bench.evaluate(task, "correct", env=None) is True
    assert bench.evaluate(task, "wrong", env=None) is False
    assert len(calls) == 2
