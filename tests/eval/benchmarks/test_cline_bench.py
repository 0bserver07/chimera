"""Tests for the Cline Bench benchmark adapter.

Covers loading (per-task-dir ``task.json``, flat ``*.json``, JSON array,
JSONL, envelope, single dict), absent-dataset behavior, field-alias mapping,
``limit``, the JSON-safe task-dict shape, and the binary ``evaluate`` grading
path via the env ``run_command`` / ``run_tests`` hooks.

No network, no Docker, no LLM: a duck-typed fake env drives ``evaluate``.

Note: unlike its sibling adapters, ``ClineBench.__init__`` takes no validated
enum (``split`` / ``mode`` / ``track``), so there is no ctor-``ValueError`` case
to assert here — the constructor simply loads whichever source is supplied.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chimera.eval.benchmarks.cline_bench import ClineBench, ClineBenchTask


class _CmdEnv:
    """Env exposing only ``run_command`` (the preferred grading hook)."""

    def __init__(
        self,
        *,
        success: bool | None = None,
        returncode: int | None = None,
        raise_exc: bool = False,
    ) -> None:
        self._success = success
        self._returncode = returncode
        self._raise = raise_exc
        self.commands: list[str] = []

    def run_command(self, cmd: str):
        self.commands.append(cmd)
        if self._raise:
            raise RuntimeError("boom")
        ns = SimpleNamespace()
        if self._success is not None:
            ns.success = self._success
        if self._returncode is not None:
            ns.returncode = self._returncode
        return ns


class _TestsEnv:
    """Env exposing only ``run_tests`` (the fallback grading hook)."""

    def __init__(self, *, all_passed: bool = False, raise_exc: bool = False) -> None:
        self._all_passed = all_passed
        self._raise = raise_exc

    def run_tests(self):
        if self._raise:
            raise RuntimeError("boom")
        return SimpleNamespace(all_passed=self._all_passed)


def _record(**over: object) -> dict:
    base: dict = {
        "task_id": "cline-1",
        "instructions": "Fix the bug in foo().",
        "repo_snapshot": "org/repo@abc",
        "docker_image": "cline/env:1",
        "test_command": "pytest -q",
        "setup_commands": ["pip install -e ."],
        "metadata": {"domain": "web"},
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- name


def test_name_is_cline_bench() -> None:
    assert ClineBench().name() == "cline-bench"


# ------------------------------------------------------------------------- loading


def test_no_dataset_yields_no_tasks() -> None:
    """Absent dataset -> empty task list without raising."""
    assert ClineBench().tasks() == []


def test_missing_dir_raises() -> None:
    with pytest.raises(FileNotFoundError):
        ClineBench(dataset_dir="/no/such/cline/dir")


def test_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        ClineBench(dataset_path="/no/such/cline.json")


def test_load_per_task_subdirs(tmp_path: Path) -> None:
    for name in ("beta", "alpha"):
        sub = tmp_path / name
        sub.mkdir()
        (sub / "task.json").write_text(json.dumps(_record(task_id="")))
    bench = ClineBench(dataset_dir=str(tmp_path))
    tasks = bench.tasks()
    assert len(tasks) == 2
    # task_id absent in the record -> derived from the parent dir name.
    assert {t["id"] for t in tasks} == {"alpha", "beta"}


def test_load_flat_json_files_uses_stem_as_id(tmp_path: Path) -> None:
    (tmp_path / "t1.json").write_text(json.dumps(_record(task_id="")))
    (tmp_path / "t2.json").write_text(json.dumps(_record(task_id="")))
    ids = {t["id"] for t in ClineBench(dataset_dir=str(tmp_path)).tasks()}
    assert ids == {"t1", "t2"}


def test_load_json_array_file(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([_record(task_id="a"), _record(task_id="b")]))
    assert len(ClineBench(dataset_path=str(path)).tasks()) == 2


def test_load_envelope_file(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps({"tasks": [_record(task_id="a")]}))
    assert len(ClineBench(dataset_path=str(path)).tasks()) == 1


def test_load_single_dict_file(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps(_record(task_id="solo")))
    tasks = ClineBench(dataset_path=str(path)).tasks()
    assert [t["id"] for t in tasks] == ["solo"]


def test_load_jsonl_file(tmp_path: Path) -> None:
    path = tmp_path / "ds.jsonl"
    path.write_text("\n".join(json.dumps(_record(task_id=f"j{i}")) for i in range(3)))
    assert len(ClineBench(dataset_path=str(path)).tasks()) == 3


def test_limit_respected_on_file(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([_record(task_id=f"t{i}") for i in range(5)]))
    assert len(ClineBench(dataset_path=str(path), limit=2).tasks()) == 2


def test_field_alias_mapping(tmp_path: Path) -> None:
    """Alternate key names (id/prompt/repo/image/test/setup) are honored."""
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([{
        "id": "aliased",
        "prompt": "Do the thing.",
        "repo": "org/x@1",
        "image": "img:2",
        "test": "make test",
        "setup": ["make deps"],
    }]))
    task = ClineBench(dataset_path=str(path)).tasks()[0]
    assert task["id"] == "aliased"
    assert task["prompt"] == "Do the thing."
    assert task["repo_snapshot"] == "org/x@1"
    assert task["docker_image"] == "img:2"
    assert task["test_command"] == "make test"
    assert task["setup_commands"] == ["make deps"]


def test_task_dict_shape_is_json_safe(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([_record(task_id="s")]))
    task = ClineBench(dataset_path=str(path)).tasks()[0]
    for key in ("id", "prompt", "description", "repo_snapshot", "docker_image",
                "test_command", "setup_commands", "metadata"):
        assert key in task
    json.dumps(task)  # must not raise


def test_add_task_and_instances() -> None:
    bench = ClineBench()
    bench.add_task(ClineBenchTask(task_id="x", instructions="i", test_command="t"))
    assert [t["id"] for t in bench.tasks()] == ["x"]
    assert bench.instances[0].task_id == "x"


# ------------------------------------------------------------------------ evaluate


def test_evaluate_none_env_is_false() -> None:
    task = ClineBenchTask(task_id="t", instructions="i", test_command="pytest").to_task()
    assert ClineBench().evaluate(task, "", None) is False


def test_evaluate_run_command_success_true() -> None:
    task = ClineBenchTask(task_id="t", instructions="i", test_command="pytest").to_task()
    env = _CmdEnv(success=True)
    assert ClineBench().evaluate(task, "", env) is True
    assert env.commands == ["pytest"]


def test_evaluate_run_command_success_false() -> None:
    task = ClineBenchTask(task_id="t", instructions="i", test_command="pytest").to_task()
    assert ClineBench().evaluate(task, "", _CmdEnv(success=False)) is False


def test_evaluate_run_command_returncode_zero_is_pass() -> None:
    task = ClineBenchTask(task_id="t", instructions="i", test_command="pytest").to_task()
    assert ClineBench().evaluate(task, "", _CmdEnv(returncode=0)) is True


def test_evaluate_run_command_returncode_nonzero_is_fail() -> None:
    task = ClineBenchTask(task_id="t", instructions="i", test_command="pytest").to_task()
    assert ClineBench().evaluate(task, "", _CmdEnv(returncode=3)) is False


def test_evaluate_run_command_no_signal_is_false() -> None:
    """A result carrying neither success nor returncode fails closed."""
    task = ClineBenchTask(task_id="t", instructions="i", test_command="pytest").to_task()
    assert ClineBench().evaluate(task, "", _CmdEnv()) is False


def test_evaluate_run_command_exception_is_false() -> None:
    task = ClineBenchTask(task_id="t", instructions="i", test_command="pytest").to_task()
    assert ClineBench().evaluate(task, "", _CmdEnv(raise_exc=True)) is False


def test_evaluate_falls_back_to_run_tests() -> None:
    """No run_command hook -> the run_tests path grades the task."""
    task = ClineBenchTask(task_id="t", instructions="i", test_command="pytest").to_task()
    assert ClineBench().evaluate(task, "", _TestsEnv(all_passed=True)) is True
    assert ClineBench().evaluate(task, "", _TestsEnv(all_passed=False)) is False


def test_evaluate_run_tests_exception_is_false() -> None:
    task = ClineBenchTask(task_id="t", instructions="i", test_command="pytest").to_task()
    assert ClineBench().evaluate(task, "", _TestsEnv(raise_exc=True)) is False


def test_evaluate_no_test_command_and_no_run_tests_is_false() -> None:
    """Without a test command, the run_command branch is skipped entirely."""
    task = ClineBenchTask(task_id="t", instructions="i", test_command="").to_task()
    assert ClineBench().evaluate(task, "", _CmdEnv(success=True)) is False
