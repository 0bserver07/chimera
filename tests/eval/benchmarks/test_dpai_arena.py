"""Tests for the DPAI Arena benchmark adapter.

Covers ctor ``track`` validation, the ``name`` string, absent-dataset
behavior, loading (JSON array / JSONL / envelope), the load-time ``track`` and
``language`` filters, ``limit``, the task-dict shape, and the per-track
``evaluate`` dispatch (issue-to-patch, coverage, rubric tracks, and the
non-empty-output fallback for unknown tracks).

No network, no Docker: a duck-typed fake env drives ``evaluate``.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chimera.eval.benchmarks.dpai_arena import SUPPORTED_TRACKS, DPAIArena, DPAITask


class _PatchEnv:
    """Env with write_file / run_command / run_tests for the patch track."""

    def __init__(self, *, apply_ok: bool = True, tests_ok: bool = True) -> None:
        self._apply_ok = apply_ok
        self._tests_ok = tests_ok
        self.files: dict[str, str] = {}
        self.commands: list[str] = []

    def write_file(self, path: str, content: str) -> None:
        self.files[path] = content

    def run_command(self, cmd: str):
        self.commands.append(cmd)
        return SimpleNamespace(success=self._apply_ok)

    def run_tests(self):
        return SimpleNamespace(all_passed=self._tests_ok)


def _item(**over: object) -> dict:
    base: dict = {
        "instance_id": "dpai-1",
        "track": "issue-to-patch",
        "repo": "spring-projects/spring-boot",
        "base_commit": "deadbeef",
        "language": "java",
        "framework": "spring",
        "problem_statement": "Fix the NPE in the controller.",
        "test_patch": "diff --git a b",
        "build_tool": "maven",
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- ctor


def test_bad_track_raises() -> None:
    with pytest.raises(ValueError, match="Unknown track"):
        DPAIArena(track="not-a-track")


def test_all_track_is_allowed() -> None:
    assert DPAIArena(track="all").track == "all"


def test_every_supported_track_constructs() -> None:
    for track in SUPPORTED_TRACKS:
        assert DPAIArena(track=track).track == track


def test_name_includes_track() -> None:
    assert DPAIArena(track="coverage").name() == "dpai-arena[coverage]"


# ------------------------------------------------------------------------- loading


def test_no_dataset_yields_no_tasks() -> None:
    assert DPAIArena().tasks() == []


def test_missing_path_raises() -> None:
    with pytest.raises(FileNotFoundError):
        DPAIArena(dataset_path="/no/such/dpai.jsonl")


def test_load_json_array(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([_item(instance_id="a"), _item(instance_id="b")]))
    assert len(DPAIArena(dataset_path=str(path)).tasks()) == 2


def test_load_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "ds.jsonl"
    path.write_text("\n".join(json.dumps(_item(instance_id=f"j{i}")) for i in range(3)))
    assert len(DPAIArena(dataset_path=str(path)).tasks()) == 3


def test_load_envelope(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps({"tasks": [_item(instance_id="a")]}))
    assert len(DPAIArena(dataset_path=str(path)).tasks()) == 1


def test_track_filter_drops_nonmatching(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([
        _item(instance_id="a", track="issue-to-patch"),
        _item(instance_id="b", track="coverage"),
    ]))
    # Default track is issue-to-patch -> only that instance survives.
    tasks = DPAIArena(dataset_path=str(path)).tasks()
    assert [t["id"] for t in tasks] == ["a"]


def test_track_all_keeps_every_instance(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([
        _item(instance_id="a", track="issue-to-patch"),
        _item(instance_id="b", track="coverage"),
    ]))
    assert len(DPAIArena(dataset_path=str(path), track="all").tasks()) == 2


def test_language_filter(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([
        _item(instance_id="a", language="java"),
        _item(instance_id="b", language="kotlin"),
    ]))
    tasks = DPAIArena(dataset_path=str(path), language="kotlin").tasks()
    assert [t["id"] for t in tasks] == ["b"]


def test_limit_respected(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([_item(instance_id=f"t{i}") for i in range(5)]))
    assert len(DPAIArena(dataset_path=str(path), limit=2).tasks()) == 2


def test_task_dict_shape(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([_item(instance_id="s")]))
    task = DPAIArena(dataset_path=str(path)).tasks()[0]
    for key in ("id", "track", "prompt", "description", "repo", "base_commit",
                "language", "framework", "hints", "test_patch", "pr_diff",
                "target_files", "build_tool", "metadata"):
        assert key in task
    json.dumps(task)


def test_add_instance_and_instances() -> None:
    bench = DPAIArena()
    bench.add_instance(DPAITask(instance_id="x", track="coverage", repo="r", base_commit="c"))
    assert [t["id"] for t in bench.tasks()] == ["x"]
    assert bench.instances[0].instance_id == "x"


# ------------------------------------------------------------------------ evaluate


def test_evaluate_none_env_is_false() -> None:
    assert DPAIArena().evaluate(_item(), "output", None) is False


def test_evaluate_patch_track_passes_when_tests_pass() -> None:
    task = DPAITask(
        instance_id="p", track="issue-to-patch", repo="r", base_commit="c",
        test_patch="diff --git a b",
    ).to_task()
    env = _PatchEnv(apply_ok=True, tests_ok=True)
    assert DPAIArena().evaluate(task, "", env) is True
    assert env.commands == ["git apply _dpai_test_patch.diff"]


def test_evaluate_patch_track_fails_when_apply_fails() -> None:
    task = DPAITask(
        instance_id="p", track="issue-to-patch", repo="r", base_commit="c",
        test_patch="diff --git a b",
    ).to_task()
    assert DPAIArena().evaluate(task, "", _PatchEnv(apply_ok=False)) is False


def test_evaluate_patch_track_fails_when_tests_fail() -> None:
    task = DPAITask(
        instance_id="p", track="issue-to-patch", repo="r", base_commit="c",
        test_patch="diff --git a b",
    ).to_task()
    assert DPAIArena().evaluate(task, "", _PatchEnv(apply_ok=True, tests_ok=False)) is False


def test_evaluate_coverage_track_uses_run_tests() -> None:
    task = DPAITask(instance_id="c", track="coverage", repo="r", base_commit="c").to_task()
    bench = DPAIArena(track="coverage")
    assert bench.evaluate(task, "", _PatchEnv(tests_ok=True)) is True
    assert bench.evaluate(task, "", _PatchEnv(tests_ok=False)) is False


def test_evaluate_rubric_track_scores_substantive_output() -> None:
    task = DPAITask(instance_id="r", track="pr-review", repo="r", base_commit="c").to_task()
    bench = DPAIArena(track="pr-review")
    env = SimpleNamespace()  # rubric tracks ignore env beyond the None guard
    assert bench.evaluate(task, "x" * 21, env) is True
    assert bench.evaluate(task, "short", env) is False


def test_evaluate_unknown_track_falls_back_to_length() -> None:
    """A track outside the known set falls through to the >10-char heuristic."""
    task = {"id": "u", "track": "mystery-track"}
    env = SimpleNamespace()
    assert DPAIArena(track="all").evaluate(task, "x" * 11, env) is True
    assert DPAIArena(track="all").evaluate(task, "tiny", env) is False
