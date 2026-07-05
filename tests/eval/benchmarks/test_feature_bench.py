"""Tests for the FeatureBench benchmark adapter.

Covers the ``name`` string, absent-dataset behavior, loading (JSON array /
JSONL / envelope), the ``_row_to_task`` alias + metadata catch-all mapping,
the ``level_filter`` and ``limit`` filters, the task-dict shape, and the
``evaluate`` resolution order (``run_tests`` with/without positional args, the
``run_command`` pytest path, and the non-empty-output fallback).

No network, no Docker: duck-typed fake envs drive ``evaluate``.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chimera.eval.benchmarks.feature_bench import FeatureBench, FeatureBenchTask


class _TestsEnv:
    """Env whose ``run_tests`` accepts the optional test-files argument."""

    def __init__(self, *, all_passed: bool = True) -> None:
        self._all_passed = all_passed
        self.called_with: object = "unset"

    def run_tests(self, test_files=None):
        self.called_with = test_files
        return SimpleNamespace(all_passed=self._all_passed)


class _NoArgTestsEnv:
    """Env whose ``run_tests`` rejects positional args (TypeError fallback)."""

    def __init__(self, *, all_passed: bool = True) -> None:
        self._all_passed = all_passed
        self.calls = 0

    def run_tests(self):
        self.calls += 1
        return SimpleNamespace(all_passed=self._all_passed)


class _CmdEnv:
    """Env exposing only ``run_command`` (the pytest path)."""

    def __init__(self, *, success: bool = True) -> None:
        self._success = success
        self.commands: list[str] = []

    def run_command(self, cmd: str):
        self.commands.append(cmd)
        return SimpleNamespace(success=self._success)


def _row(**over: object) -> dict:
    base: dict = {
        "task_id": "sympy__sympy-1-lv1",
        "repo": "sympy/sympy",
        "base_commit": "cafef00d",
        "level": "lv1",
        "prompt": "Implement the new feature.",
        "test_files": ["tests/test_feature.py"],
        "masked_files": ["sympy/core/feature.py"],
        "docker_image": "featurebench/sympy:1",
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- name


def test_name_includes_split() -> None:
    assert FeatureBench(split="lite").name() == "feature-bench-lite"
    assert FeatureBench(split="full").name() == "feature-bench-full"


# ------------------------------------------------------------------------- loading


def test_no_dataset_yields_no_tasks() -> None:
    assert FeatureBench().tasks() == []


def test_missing_path_raises() -> None:
    with pytest.raises(FileNotFoundError):
        FeatureBench(dataset_path="/no/such/fb.json")


def test_load_json_array(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([_row(task_id="a"), _row(task_id="b")]))
    assert len(FeatureBench(dataset_path=str(path)).tasks()) == 2


def test_load_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "ds.jsonl"
    path.write_text("\n".join(json.dumps(_row(task_id=f"j{i}")) for i in range(3)))
    assert len(FeatureBench(dataset_path=str(path)).tasks()) == 3


def test_load_envelope(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps({"tasks": [_row(task_id="a")]}))
    assert len(FeatureBench(dataset_path=str(path)).tasks()) == 1


def test_row_alias_and_metadata_catch_all(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([{
        "instance_id": "aliased",
        "repository": "org/x",
        "commit": "abc",
        "task_level": "lv2",
        "problem_statement": "Build it.",
        "tests": ["t/test_x.py"],
        "image": "img:2",
        "difficulty": "hard",  # unknown key -> metadata
    }]))
    task = FeatureBench(dataset_path=str(path)).tasks()[0]
    assert task["id"] == "aliased"
    assert task["repo"] == "org/x"
    assert task["base_commit"] == "abc"
    assert task["level"] == "lv2"
    assert task["prompt"] == "Build it."
    assert task["test_files"] == ["t/test_x.py"]
    assert task["docker_image"] == "img:2"
    assert task["metadata"]["difficulty"] == "hard"


def test_level_filter(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([
        _row(task_id="a", level="lv1"),
        _row(task_id="b", level="lv2"),
    ]))
    tasks = FeatureBench(dataset_path=str(path), level_filter="lv2").tasks()
    assert [t["id"] for t in tasks] == ["b"]


def test_limit_respected(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([_row(task_id=f"t{i}") for i in range(5)]))
    assert len(FeatureBench(dataset_path=str(path), limit=2).tasks()) == 2


def test_task_dict_shape(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([_row(task_id="s")]))
    task = FeatureBench(dataset_path=str(path)).tasks()[0]
    for key in ("id", "prompt", "description", "repo", "base_commit", "level",
                "test_files", "masked_files", "docker_image", "metadata"):
        assert key in task
    json.dumps(task)


def test_add_task() -> None:
    bench = FeatureBench()
    bench.add_task(FeatureBenchTask(task_id="x", repo="r", base_commit="c"))
    assert [t["id"] for t in bench.tasks()] == ["x"]


# ------------------------------------------------------------------------ evaluate


def test_evaluate_none_env_is_false() -> None:
    assert FeatureBench().evaluate(_row(), "output", None) is False


def test_evaluate_run_tests_with_test_files() -> None:
    task = FeatureBenchTask(
        task_id="t", repo="r", base_commit="c", test_files=["tests/test_a.py"],
    ).to_task()
    env = _TestsEnv(all_passed=True)
    assert FeatureBench().evaluate(task, "", env) is True
    assert env.called_with == ["tests/test_a.py"]
    assert FeatureBench().evaluate(task, "", _TestsEnv(all_passed=False)) is False


def test_evaluate_run_tests_typeerror_falls_back_to_no_arg() -> None:
    """When run_tests rejects positional args, the no-arg call is retried."""
    task = FeatureBenchTask(
        task_id="t", repo="r", base_commit="c", test_files=["tests/test_a.py"],
    ).to_task()
    env = _NoArgTestsEnv(all_passed=True)
    assert FeatureBench().evaluate(task, "", env) is True
    assert env.calls == 1


def test_evaluate_run_command_pytest_path() -> None:
    task = FeatureBenchTask(
        task_id="t", repo="r", base_commit="c", test_files=["tests/test_a.py"],
    ).to_task()
    env = _CmdEnv(success=True)
    assert FeatureBench().evaluate(task, "", env) is True
    assert env.commands == ["python -m pytest -x tests/test_a.py"]
    assert FeatureBench().evaluate(task, "", _CmdEnv(success=False)) is False


def test_evaluate_fallback_heuristic_when_no_hooks() -> None:
    task = FeatureBenchTask(task_id="t", repo="r", base_commit="c").to_task()
    env = SimpleNamespace()  # no run_tests, no run_command
    assert FeatureBench().evaluate(task, "x" * 11, env) is True
    assert FeatureBench().evaluate(task, "tiny", env) is False
