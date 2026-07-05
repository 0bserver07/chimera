"""Tests for the SWE-PolyBench benchmark adapter.

Covers ctor ``split`` / ``language`` validation, the ``name`` string, absent-
dataset behavior, loading (JSON array / JSONL / envelope), the ``split`` and
``language`` filters, ``limit``, the task-dict shape, the ``evaluate`` grading
paths (gold test-patch apply, ``run_tests``, the language-appropriate
``run_command`` fallback, and the non-empty-output last resort), plus the
``localization_accuracy`` and ``cst_node_recall`` metrics.

No network, no Docker: duck-typed fake envs drive ``evaluate``.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chimera.eval.benchmarks.swe_polybench import SWEPolyBench, SWEPolyBenchInstance


class _PatchTestsEnv:
    """Env with write_file / run_command (git apply) / run_tests."""

    def __init__(self, *, apply_ok: bool = True, tests_ok: bool = True) -> None:
        self._apply_ok = apply_ok
        self._tests_ok = tests_ok
        self.commands: list[str] = []

    def write_file(self, path: str, content: str) -> None:
        pass

    def run_command(self, cmd: str):
        self.commands.append(cmd)
        return SimpleNamespace(success=self._apply_ok)

    def run_tests(self):
        return SimpleNamespace(all_passed=self._tests_ok)


class _CmdOnlyEnv:
    """Env exposing only ``run_command`` (language-command path)."""

    def __init__(self, *, success: bool = True) -> None:
        self._success = success
        self.commands: list[str] = []

    def run_command(self, cmd: str):
        self.commands.append(cmd)
        return SimpleNamespace(success=self._success)


def _item(**over: object) -> dict:
    base: dict = {
        "instance_id": "poly-1",
        "repo": "org/repo",
        "base_commit": "abc",
        "problem_statement": "Fix the bug.",
        "language": "python",
        "task_type": "bug_fix",
        "test_patch": "",
        "modified_files": ["src/a.py"],
        "cst_nodes": ["a.py::foo"],
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- ctor


def test_bad_split_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported split"):
        SWEPolyBench(split="mega")


def test_bad_language_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported language"):
        SWEPolyBench(language="cobol")


def test_name_includes_split_and_language() -> None:
    assert SWEPolyBench(split="pb500").name() == "swe-polybench-pb500"
    assert SWEPolyBench(split="full", language="java").name() == "swe-polybench-full-java"


# ------------------------------------------------------------------------- loading


def test_no_dataset_yields_no_tasks() -> None:
    assert SWEPolyBench().tasks() == []


def test_missing_path_raises() -> None:
    with pytest.raises(FileNotFoundError):
        SWEPolyBench(dataset_path="/no/such/poly.json")


def test_load_json_array(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([_item(instance_id="a"), _item(instance_id="b")]))
    assert len(SWEPolyBench(dataset_path=str(path)).tasks()) == 2


def test_load_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "ds.jsonl"
    path.write_text("\n".join(json.dumps(_item(instance_id=f"j{i}")) for i in range(3)))
    assert len(SWEPolyBench(dataset_path=str(path)).tasks()) == 3


def test_load_envelope(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps({"tasks": [_item(instance_id="a")]}))
    assert len(SWEPolyBench(dataset_path=str(path)).tasks()) == 1


def test_split_filter(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([
        _item(instance_id="a", split="pb500"),
        _item(instance_id="b", split="verified"),
    ]))
    # Instance-level "split" != the benchmark split -> filtered out.
    tasks = SWEPolyBench(dataset_path=str(path), split="pb500").tasks()
    assert [t["id"] for t in tasks] == ["a"]


def test_language_filter(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([
        _item(instance_id="a", language="python"),
        _item(instance_id="b", language="java"),
    ]))
    tasks = SWEPolyBench(dataset_path=str(path), language="java").tasks()
    assert [t["id"] for t in tasks] == ["b"]


def test_limit_respected(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([_item(instance_id=f"t{i}") for i in range(5)]))
    assert len(SWEPolyBench(dataset_path=str(path), limit=2).tasks()) == 2


def test_task_dict_shape(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([_item(instance_id="s")]))
    task = SWEPolyBench(dataset_path=str(path)).tasks()[0]
    for key in ("id", "prompt", "description", "repo", "base_commit", "language",
                "task_type", "test_patch", "modified_files", "cst_nodes", "hints"):
        assert key in task
    json.dumps(task)


def test_add_instance_and_instances() -> None:
    bench = SWEPolyBench()
    bench.add_instance(SWEPolyBenchInstance(
        instance_id="x", repo="r", base_commit="c", problem_statement="p"))
    assert [t["id"] for t in bench.tasks()] == ["x"]
    assert bench.instances[0].instance_id == "x"


# ------------------------------------------------------------------------ evaluate


def test_evaluate_none_env_is_false() -> None:
    assert SWEPolyBench().evaluate(_item(), "output", None) is False


def test_evaluate_patch_apply_failure_is_false() -> None:
    task = _item(test_patch="diff --git a b")
    assert SWEPolyBench().evaluate(task, "", _PatchTestsEnv(apply_ok=False)) is False


def test_evaluate_run_tests_pass_and_fail() -> None:
    task = _item(test_patch="diff --git a b")
    assert SWEPolyBench().evaluate(task, "", _PatchTestsEnv(tests_ok=True)) is True
    assert SWEPolyBench().evaluate(task, "", _PatchTestsEnv(tests_ok=False)) is False


def test_evaluate_language_command_path() -> None:
    task = _item(language="python", test_patch="")  # no patch, no run_tests
    env = _CmdOnlyEnv(success=True)
    assert SWEPolyBench().evaluate(task, "", env) is True
    assert env.commands == ["pytest -x"]
    assert SWEPolyBench().evaluate(_item(test_patch=""), "", _CmdOnlyEnv(success=False)) is False


def test_evaluate_fallback_heuristic() -> None:
    task = _item(test_patch="")
    env = SimpleNamespace()  # no hooks at all
    assert SWEPolyBench().evaluate(task, "x" * 11, env) is True
    assert SWEPolyBench().evaluate(task, "tiny", env) is False


# ------------------------------------------------------------------------- metrics


def test_localization_accuracy() -> None:
    bench = SWEPolyBench()
    task = {"modified_files": ["a.py", "b.py"]}
    assert bench.localization_accuracy(task, ["a.py"]) == 0.5
    assert bench.localization_accuracy(task, ["a.py", "b.py"]) == 1.0
    assert bench.localization_accuracy({"modified_files": []}, ["a.py"]) == 0.0


def test_cst_node_recall() -> None:
    bench = SWEPolyBench()
    task = {"cst_nodes": ["a::f", "a::g"]}
    assert bench.cst_node_recall(task, ["a::f"]) == 0.5
    assert bench.cst_node_recall({"cst_nodes": []}, ["a::f"]) == 0.0
