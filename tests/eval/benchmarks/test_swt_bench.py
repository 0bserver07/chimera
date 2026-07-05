"""Tests for the SWT-Bench (test-generation) benchmark adapter.

Covers ctor ``mode`` validation, the ``name`` string and ``mode`` / ``split``
properties, absent-dataset behavior, loading (JSON array / JSONL / envelope)
with the ``FAIL_TO_PASS`` / ``PASS_TO_PASS`` (and lowercase-alias) mapping and
``limit``, the task-dict shape, and both ``evaluate`` grading flows:

* ``unit_test`` mode — apply agent test patch, require FAIL on buggy code,
  apply gold patch, require PASS afterwards (F2P);
* ``reproduction`` mode — run the agent script, require non-zero exit on buggy
  code, apply gold patch, require zero exit afterwards.

The no-env fallback (non-empty-output heuristic) and the missing-gold-patch
guard are asserted too. No network, no Docker: an ordered-result fake env drives
the multi-step flows.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chimera.eval.benchmarks.swt_bench import SWTBench, SWTBenchInstance


class _SWTEnv:
    """Fake env returning queued results for run_command / run_tests calls.

    ``cmd_results`` feeds successive ``run_command`` calls (git apply /
    script runs); ``test_results`` feeds successive ``run_tests`` calls.
    """

    def __init__(
        self,
        *,
        cmd_results: list[bool] | None = None,
        test_results: list[bool] | None = None,
    ) -> None:
        self._cmd = list(cmd_results or [])
        self._tests = list(test_results or [])
        self.commands: list[str] = []

    def write_file(self, path: str, content: str) -> None:
        pass

    def run_command(self, cmd: str):
        self.commands.append(cmd)
        return SimpleNamespace(success=self._cmd.pop(0))

    def run_tests(self):
        return SimpleNamespace(all_passed=self._tests.pop(0))


def _item(**over: object) -> dict:
    base: dict = {
        "instance_id": "swt-1",
        "repo": "org/repo",
        "base_commit": "abc",
        "problem_statement": "Reproduce the reported bug.",
        "patch": "diff --git gold",
        "test_patch": "diff --git tests",
        "FAIL_TO_PASS": ["test_bug"],
        "PASS_TO_PASS": ["test_other"],
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- ctor


def test_bad_mode_raises() -> None:
    with pytest.raises(ValueError, match="mode must be"):
        SWTBench(mode="freestyle")


def test_name_and_properties() -> None:
    bench = SWTBench(split="verified", mode="reproduction")
    assert bench.name() == "swt-bench"
    assert bench.mode == "reproduction"
    assert bench.split == "verified"


# ------------------------------------------------------------------------- loading


def test_no_dataset_yields_no_tasks() -> None:
    assert SWTBench().tasks() == []


def test_missing_path_raises() -> None:
    with pytest.raises(FileNotFoundError):
        SWTBench(dataset_path="/no/such/swt.json")


def test_load_json_array(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([_item(instance_id="a"), _item(instance_id="b")]))
    assert len(SWTBench(dataset_path=str(path)).tasks()) == 2


def test_load_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "ds.jsonl"
    path.write_text("\n".join(json.dumps(_item(instance_id=f"j{i}")) for i in range(3)))
    assert len(SWTBench(dataset_path=str(path)).tasks()) == 3


def test_load_envelope(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps({"tasks": [_item(instance_id="a")]}))
    assert len(SWTBench(dataset_path=str(path)).tasks()) == 1


def test_fail_to_pass_uppercase_and_lowercase_aliases(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([
        _item(instance_id="upper", FAIL_TO_PASS=["t1"], PASS_TO_PASS=["t2"]),
        {
            "instance_id": "lower",
            "repo": "r",
            "base_commit": "c",
            "problem_statement": "p",
            "fail_to_pass": ["t3"],
            "pass_to_pass": ["t4"],
        },
    ]))
    tasks = {t["id"]: t for t in SWTBench(dataset_path=str(path)).tasks()}
    assert tasks["upper"]["FAIL_TO_PASS"] == ["t1"]
    assert tasks["lower"]["FAIL_TO_PASS"] == ["t3"]
    assert tasks["lower"]["PASS_TO_PASS"] == ["t4"]


def test_limit_respected(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([_item(instance_id=f"t{i}") for i in range(5)]))
    assert len(SWTBench(dataset_path=str(path), limit=2).tasks()) == 2


def test_task_dict_shape(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([_item(instance_id="s")]))
    task = SWTBench(dataset_path=str(path)).tasks()[0]
    for key in ("id", "prompt", "description", "repo", "base_commit", "hints",
                "test_patch", "patch", "FAIL_TO_PASS", "PASS_TO_PASS"):
        assert key in task
    json.dumps(task)


def test_add_instance_and_instances() -> None:
    bench = SWTBench()
    bench.add_instance(SWTBenchInstance(
        instance_id="x", repo="r", base_commit="c", problem_statement="p"))
    assert [t["id"] for t in bench.tasks()] == ["x"]
    assert bench.instances[0].instance_id == "x"


# ------------------------------------------------------------------ evaluate (base)


def test_evaluate_no_env_uses_length_heuristic() -> None:
    bench = SWTBench()
    assert bench.evaluate(_item(), "x" * 11, None) is True
    assert bench.evaluate(_item(), "tiny", None) is False


def test_evaluate_missing_gold_patch_is_false() -> None:
    bench = SWTBench()
    task = _item(patch="")
    assert bench.evaluate(task, "some agent test", _SWTEnv()) is False


# ---------------------------------------------------------- evaluate (unit_test F2P)


def test_unit_test_f2p_success() -> None:
    """Agent test fails on buggy code, then passes after the gold patch."""
    bench = SWTBench(mode="unit_test")
    env = _SWTEnv(cmd_results=[True, True], test_results=[False, True])
    assert bench.evaluate(_item(), "test diff", env) is True


def test_unit_test_fails_when_tests_already_pass_on_buggy() -> None:
    """A test that passes on buggy code does not reproduce the bug."""
    bench = SWTBench(mode="unit_test")
    env = _SWTEnv(cmd_results=[True], test_results=[True])
    assert bench.evaluate(_item(), "test diff", env) is False


def test_unit_test_fails_when_agent_patch_does_not_apply() -> None:
    bench = SWTBench(mode="unit_test")
    env = _SWTEnv(cmd_results=[False], test_results=[])
    assert bench.evaluate(_item(), "bad diff", env) is False


def test_unit_test_missing_contract_is_false() -> None:
    """An env without write_file/run_command/run_tests cannot grade."""
    bench = SWTBench(mode="unit_test")
    assert bench.evaluate(_item(), "test diff", SimpleNamespace()) is False


# ------------------------------------------------------ evaluate (reproduction mode)


def test_reproduction_success() -> None:
    """Script fails on buggy code (nonzero), passes after the gold patch."""
    bench = SWTBench(mode="reproduction")
    # run order: repro-on-buggy(False), gold-apply(True), repro-post(True)
    env = _SWTEnv(cmd_results=[False, True, True])
    assert bench.evaluate(_item(), "print('repro')", env) is True


def test_reproduction_fails_when_script_passes_on_buggy() -> None:
    bench = SWTBench(mode="reproduction")
    env = _SWTEnv(cmd_results=[True])
    assert bench.evaluate(_item(), "print('ok')", env) is False
