"""Faithful FAIL_TO_PASS / PASS_TO_PASS grading for MultiSWE-bench — offline.

MultiSWE-bench grades by a language's native runner. For **Python** instances
that carry named test lists, this module adds the official SWE-bench resolve
contract (run exactly the FAIL_TO_PASS / PASS_TO_PASS node ids) ahead of the
blanket runner. Covered here:

* ``_named_test_list`` normalizing the upstream ``f2p_tests`` / ``p2p_tests``
  *mapping* (name -> execution record), the SWE-bench JSON-string encoding, and
  native lists.
* Field surfacing onto :class:`MultiSWEBenchInstance` + :meth:`to_task`, and the
  loader parsing them for Python only (never faking ids for other languages).
* :meth:`MultiSWEBench.evaluate` taking the faithful path — exact node ids reach
  pytest, the resolve logic, chunking, and the shared skip diagnostics
  (toolchain missing / patch failed) — plus back-compat: lists-absent Python and
  every non-Python instance still route through the language runner.
* An honest reality check: the staged Python subset carries NO named tests
  (the staging transform drops the upstream ``*_tests`` records), so it grades
  via the runner today.

A fake env captures every ``run_command`` so assertions are exact; no cloud,
container, or LLM is touched.
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from chimera.eval.benchmarks.multi_swe_bench import (
    MultiSWEBench,
    MultiSWEBenchInstance,
    _named_test_list,
)
from chimera.eval.benchmarks.runners.base import SkipReason


# --------------------------------------------------------------------------- #
# Fake environment
# --------------------------------------------------------------------------- #


class FakeEnv:
    """Env double capturing the grading command stream.

    Args:
        fail_ids: Node ids whose presence in a pytest command makes that run
            exit non-zero (a still-failing F2P or a regressing P2P).
        apply_ok: Whether ``git apply`` of the test patch succeeds.
        toolchain_ok: Whether the ``python --version`` probe succeeds.
    """

    def __init__(
        self,
        *,
        fail_ids: frozenset[str] = frozenset(),
        apply_ok: bool = True,
        toolchain_ok: bool = True,
    ) -> None:
        self.commands: list[str] = []
        self.written: dict[str, str] = {}
        self._fail_ids = set(fail_ids)
        self._apply_ok = apply_ok
        self._toolchain_ok = toolchain_ok

    def write_file(self, path: str, content: str) -> None:
        self.written[path] = content

    def run_command(self, cmd: str):
        self.commands.append(cmd)
        if cmd == "python --version":
            return SimpleNamespace(
                success=self._toolchain_ok,
                exit_code=0 if self._toolchain_ok else 127,
            )
        if cmd.startswith("git apply"):
            return SimpleNamespace(
                success=self._apply_ok, exit_code=0 if self._apply_ok else 1
            )
        if "pytest" in cmd:
            for fid in self._fail_ids:
                if fid in cmd:
                    return SimpleNamespace(success=False, exit_code=1)
            return SimpleNamespace(success=True, exit_code=0)
        return SimpleNamespace(success=True, exit_code=0)

    @property
    def pytest_commands(self) -> list[str]:
        return [c for c in self.commands if "pytest" in c]


def _instance(
    fail_to_pass: list[str],
    pass_to_pass: list[str],
    *,
    language: str = "python",
    test_patch: str = "diff --git a/t.py b/t.py",
) -> MultiSWEBenchInstance:
    return MultiSWEBenchInstance(
        instance_id="demo__x-1",
        repo="demo/x",
        base_commit="abc123",
        problem_statement="fix the thing",
        language=language,
        test_patch=test_patch,
        fail_to_pass=fail_to_pass,
        pass_to_pass=pass_to_pass,
    )


# --------------------------------------------------------------------------- #
# _named_test_list — mapping / JSON string / list normalization
# --------------------------------------------------------------------------- #


class TestNamedTestList:
    def test_mapping_resolves_to_keys(self) -> None:
        # Upstream ``f2p_tests`` is a mapping of test name -> execution record.
        mapping = {"a/t.py::test_x": {"run": "ok"}, "a/t.py::test_y": {}}
        assert _named_test_list(mapping) == ["a/t.py::test_x", "a/t.py::test_y"]

    def test_json_string_list(self) -> None:
        assert _named_test_list(json.dumps(["a/t.py::test_a"])) == [
            "a/t.py::test_a"
        ]

    def test_native_list(self) -> None:
        assert _named_test_list(["a/t.py::test_a"]) == ["a/t.py::test_a"]

    def test_none_is_empty(self) -> None:
        assert _named_test_list(None) == []

    def test_blank_keys_dropped_from_mapping(self) -> None:
        assert _named_test_list({"a/t.py::test_a": 1, "": 2}) == ["a/t.py::test_a"]


# --------------------------------------------------------------------------- #
# Field surfacing: instance + to_task + loader
# --------------------------------------------------------------------------- #


class TestFieldSurfacing:
    def test_to_task_carries_lists(self) -> None:
        task = _instance(["t.py::f"], ["t.py::p1", "t.py::p2"]).to_task()
        assert task["fail_to_pass"] == ["t.py::f"]
        assert task["pass_to_pass"] == ["t.py::p1", "t.py::p2"]

    def test_to_task_defaults_empty(self) -> None:
        inst = MultiSWEBenchInstance(
            instance_id="x", repo="x/y", base_commit="c", problem_statement="p"
        )
        task = inst.to_task()
        assert task["fail_to_pass"] == []
        assert task["pass_to_pass"] == []

    def test_loader_parses_python_named_tests(self, tmp_path: Path) -> None:
        row = {
            "instance_id": "demo__x-1",
            "repo": "demo/x",
            "base_commit": "abc",
            "problem_statement": "fix",
            "language": "python",
            "FAIL_TO_PASS": json.dumps(["tests/test_x.py::test_a"]),
            "PASS_TO_PASS": json.dumps(
                ["tests/test_x.py::test_b", "tests/test_x.py::test_c"]
            ),
        }
        path = tmp_path / "multi.json"
        path.write_text(json.dumps([row]), encoding="utf-8")
        bench = MultiSWEBench(dataset_path=str(path))
        inst = bench.instances[0]
        assert inst.fail_to_pass == ["tests/test_x.py::test_a"]
        assert inst.pass_to_pass == [
            "tests/test_x.py::test_b",
            "tests/test_x.py::test_c",
        ]

    def test_loader_parses_upstream_tests_mapping(self, tmp_path: Path) -> None:
        # Upstream form: f2p_tests / p2p_tests as mappings.
        row = {
            "instance_id": "demo__x-2",
            "language": "python",
            "problem_statement": "fix",
            "f2p_tests": {"tests/test_x.py::test_a": {"status": "PASSED"}},
            "p2p_tests": {"tests/test_x.py::test_b": {"status": "PASSED"}},
        }
        path = tmp_path / "multi.json"
        path.write_text(json.dumps([row]), encoding="utf-8")
        inst = MultiSWEBench(dataset_path=str(path)).instances[0]
        assert inst.fail_to_pass == ["tests/test_x.py::test_a"]
        assert inst.pass_to_pass == ["tests/test_x.py::test_b"]

    def test_loader_leaves_non_python_empty(self, tmp_path: Path) -> None:
        # A Go row's named tests are not pytest node ids -> not surfaced.
        row = {
            "instance_id": "demo__g-1",
            "language": "go",
            "problem_statement": "fix",
            "FAIL_TO_PASS": json.dumps(["TestThing"]),
            "PASS_TO_PASS": json.dumps(["TestKept"]),
        }
        path = tmp_path / "multi.json"
        path.write_text(json.dumps([row]), encoding="utf-8")
        inst = MultiSWEBench(dataset_path=str(path)).instances[0]
        assert inst.fail_to_pass == []
        assert inst.pass_to_pass == []


# --------------------------------------------------------------------------- #
# evaluate — faithful pass/fail logic
# --------------------------------------------------------------------------- #


class TestFaithfulGrading:
    def test_all_green_passes(self) -> None:
        env = FakeEnv()
        task = _instance(["t.py::f1", "t.py::f2"], ["t.py::p1"]).to_task()
        assert MultiSWEBench().evaluate(task, "done", env) is True

    def test_fail_to_pass_still_failing_fails(self) -> None:
        env = FakeEnv(fail_ids=frozenset({"t.py::f1"}))
        task = _instance(["t.py::f1"], ["t.py::p1"]).to_task()
        assert MultiSWEBench().evaluate(task, "done", env) is False

    def test_pass_to_pass_regression_fails(self) -> None:
        env = FakeEnv(fail_ids=frozenset({"t.py::p1"}))
        task = _instance(["t.py::f1"], ["t.py::p1"]).to_task()
        assert MultiSWEBench().evaluate(task, "done", env) is False

    def test_exact_node_ids_reach_pytest(self) -> None:
        env = FakeEnv()
        f2p = ["a/test_x.py::test_sep[compound_model6-result6]"]
        p2p = ["a/test_x.py::test_coord", "a/test_x.py::test_cdot"]
        task = _instance(f2p, p2p).to_task()
        assert MultiSWEBench().evaluate(task, "done", env) is True
        joined = "\n".join(env.pytest_commands)
        for tid in f2p + p2p:
            assert tid in joined

    def test_parametrized_ids_are_shell_quoted(self) -> None:
        env = FakeEnv()
        pid = "a/test_x.py::test_sep[compound_model6-result6]"
        task = _instance([pid], []).to_task()
        MultiSWEBench().evaluate(task, "done", env)
        assert shlex.quote(pid) in env.pytest_commands[0]

    def test_test_patch_applied_before_named_tests(self) -> None:
        env = FakeEnv()
        task = _instance(["t.py::f1"], []).to_task()
        MultiSWEBench().evaluate(task, "done", env)
        assert env.written.get("_test_patch.diff") == task["test_patch"]
        assert "git apply _test_patch.diff" in env.commands

    def test_toolchain_missing_records_skip(self) -> None:
        env = FakeEnv(toolchain_ok=False)
        bench = MultiSWEBench()
        task = _instance(["t.py::f1"], []).to_task()
        assert bench.evaluate(task, "done", env) is False
        skips = bench.last_skip_reasons
        assert skips and skips[0][1] == "python"
        assert skips[0][2] == SkipReason.TOOLCHAIN_MISSING.value
        assert env.pytest_commands == []

    def test_failed_patch_apply_records_skip(self) -> None:
        env = FakeEnv(apply_ok=False)
        bench = MultiSWEBench()
        task = _instance(["t.py::f1"], ["t.py::p1"]).to_task()
        assert bench.evaluate(task, "done", env) is False
        skips = bench.last_skip_reasons
        assert skips and skips[0][2] == SkipReason.PATCH_FAILED.value
        assert env.pytest_commands == []  # no test ran after the failed apply

    def test_raw_uppercase_keys_are_graded(self) -> None:
        # An unprocessed row handed straight to evaluate().
        env = FakeEnv()
        task = {
            "instance_id": "demo__x-1",
            "language": "python",
            "test_patch": "",
            "FAIL_TO_PASS": json.dumps(["t.py::f1"]),
            "PASS_TO_PASS": json.dumps(["t.py::p1"]),
        }
        assert MultiSWEBench().evaluate(task, "done", env) is True
        assert any("t.py::f1" in c for c in env.pytest_commands)


class TestGradingChunking:
    def test_long_list_chunked_into_multiple_invocations(self, monkeypatch) -> None:
        # Shrink the shared chunk size so the split is observable.
        import chimera.eval.benchmarks.multi_swe_bench as mod

        monkeypatch.setattr(mod, "DEFAULT_TEST_CHUNK_SIZE", 2)
        env = FakeEnv()
        f2p = [f"t.py::f{i}" for i in range(5)]
        task = _instance(f2p, []).to_task()
        assert MultiSWEBench().evaluate(task, "done", env) is True
        assert len(env.pytest_commands) == 3  # ceil(5 / 2)
        joined = "\n".join(env.pytest_commands)
        for tid in f2p:
            assert tid in joined


# --------------------------------------------------------------------------- #
# Back-compat: the runner path is untouched for lists-absent / non-Python
# --------------------------------------------------------------------------- #


class TestFallbackBackCompat:
    def test_python_without_lists_uses_runner(self) -> None:
        # No FAIL_TO_PASS/PASS_TO_PASS -> blanket runner ("pytest ...").
        env = FakeEnv()
        task = _instance([], []).to_task()
        assert MultiSWEBench().evaluate(task, "done", env) is True
        # The runner's blanket pytest command ran, not named node ids.
        assert any(c.startswith("pytest") for c in env.commands)

    def test_non_python_with_lists_uses_runner(self) -> None:
        # A rust instance that somehow carries lists must NOT be pytest-graded.
        env = FakeEnv()
        task = _instance(
            ["should_not_pytest"], [], language="rust", test_patch=""
        ).to_task()
        # Surface lists directly on the task to prove evaluate ignores them for
        # a non-Python language (the loader would already have dropped them).
        task["fail_to_pass"] = ["should_not_pytest"]
        assert MultiSWEBench().evaluate(task, "done", env) is True
        assert env.pytest_commands == []
        assert any(c.startswith("cargo test") for c in env.commands)


# --------------------------------------------------------------------------- #
# Reality check against the staged MultiSWE-bench Python subset (skips if absent)
# --------------------------------------------------------------------------- #


_STAGED = (
    Path.home() / ".chimera" / "datasets" / "multi-swe-bench" / "python-test.json"
)


@pytest.mark.skipif(not _STAGED.exists(), reason="staged MultiSWE-bench absent")
class TestStagedDatasetReality:
    """The staged Python subset now carries named tests (faithful grading).

    ``_transform_multi_swe_row`` keeps the upstream FAIL_TO_PASS / PASS_TO_PASS
    (JSON-string lists of real pytest node ids), so these rows surface them and
    ``evaluate`` takes the named-node-id path, not the blanket runner. (Before
    2026-07-09 the transform dropped them — the honest gap this closes.)
    """

    def test_staged_rows_carry_named_tests(self) -> None:
        bench = MultiSWEBench(dataset_path=str(_STAGED), limit=10)
        assert bench.tasks(), "expected staged Python tasks"
        for inst in bench.instances:
            assert inst.fail_to_pass, "F2P should now be populated post re-stage"
            assert all("::" in nid for nid in inst.fail_to_pass)  # real node ids

    def test_staged_task_routes_to_named_grading(self) -> None:
        # With named tests present, evaluate runs the specific node ids via
        # "python -m pytest", not the runner's blanket "pytest ...".
        bench = MultiSWEBench(dataset_path=str(_STAGED), limit=1)
        task = bench.tasks()[0]
        env = FakeEnv()
        bench.evaluate(task, "done", env)
        assert any(c.startswith("python -m pytest") for c in env.commands)
