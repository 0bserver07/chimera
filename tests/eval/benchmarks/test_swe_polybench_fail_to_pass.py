"""Faithful FAIL_TO_PASS / PASS_TO_PASS grading for SWE-PolyBench — offline.

SWE-PolyBench stores its ``F2P`` / ``P2P`` columns as **Python-repr strings**
(single-quoted, so ``json.loads`` rejects them) whose entries use a colon form
``<file>:<class-or-None>:<test>`` rather than pytest ``::`` node ids. This
module covers the wiring added to :mod:`chimera.eval.benchmarks.swe_polybench`:

* ``_coerce_sequence`` decoding the Python-repr list (and JSON / native list).
* ``_polybench_node_id`` converting the colon form to a pytest node id
  (module-level, class-based, 2-segment, and the idempotent pass-through).
* Field surfacing onto :class:`SWEPolyBenchInstance` + :meth:`to_task`, and the
  loader parsing them for Python only (never faking ids for Java / JS / TS).
* :meth:`SWEPolyBench.evaluate` running exactly the named tests, with the
  official resolve logic, chunking, and shell-quoting — plus the back-compat
  fallback for Python-without-lists and for non-Python.

A fake env captures every ``run_command`` so assertions are exact; no cloud,
container, or LLM is touched.
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from chimera.eval.benchmarks.swe_polybench import (
    SWEPolyBench,
    SWEPolyBenchInstance,
    _coerce_sequence,
    _polybench_node_id,
    _polybench_test_list,
)


# --------------------------------------------------------------------------- #
# Fake environment
# --------------------------------------------------------------------------- #


class FakeEnv:
    """Env double capturing the grading command stream.

    Args:
        fail_ids: Node ids whose presence in a pytest command makes that run
            exit non-zero (a still-failing F2P or a regressing P2P).
        apply_ok: Whether ``git apply`` of the test patch succeeds.
    """

    def __init__(
        self,
        *,
        fail_ids: frozenset[str] = frozenset(),
        apply_ok: bool = True,
    ) -> None:
        self.commands: list[str] = []
        self.written: dict[str, str] = {}
        self._fail_ids = set(fail_ids)
        self._apply_ok = apply_ok

    def write_file(self, path: str, content: str) -> None:
        self.written[path] = content

    def run_command(self, cmd: str):
        self.commands.append(cmd)
        if cmd.startswith("git apply"):
            return SimpleNamespace(success=self._apply_ok)
        if "pytest" in cmd:
            for fid in self._fail_ids:
                if fid in cmd:
                    return SimpleNamespace(success=False)
            return SimpleNamespace(success=True)
        return SimpleNamespace(success=True)

    @property
    def pytest_commands(self) -> list[str]:
        return [c for c in self.commands if "pytest" in c]


class _RunTestsEnv:
    """Env exposing write_file / run_command / run_tests (blanket path)."""

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


def _instance(
    fail_to_pass: list[str],
    pass_to_pass: list[str],
    *,
    language: str = "python",
    test_patch: str = "diff --git a/t.py b/t.py",
) -> SWEPolyBenchInstance:
    return SWEPolyBenchInstance(
        instance_id="org__repo-1",
        repo="org/repo",
        base_commit="abc123",
        problem_statement="fix the thing",
        language=language,
        test_patch=test_patch,
        fail_to_pass=fail_to_pass,
        pass_to_pass=pass_to_pass,
    )


# --------------------------------------------------------------------------- #
# _coerce_sequence — decode the Python-repr / JSON list
# --------------------------------------------------------------------------- #


class TestCoerceSequence:
    def test_python_repr_single_quoted_string(self) -> None:
        # The native PolyBench encoding: a Python-repr list, not JSON.
        raw = "['a/test_x.py:None:test_a', 'a/test_x.py:C:test_b']"
        assert _coerce_sequence(raw) == [
            "a/test_x.py:None:test_a",
            "a/test_x.py:C:test_b",
        ]

    def test_json_double_quoted_string(self) -> None:
        assert _coerce_sequence('["a", "b"]') == ["a", "b"]

    def test_native_list_passthrough(self) -> None:
        assert _coerce_sequence(["a", "b"]) == ["a", "b"]

    def test_empty_list_string(self) -> None:
        assert _coerce_sequence("[]") == []

    def test_none_and_blank(self) -> None:
        assert _coerce_sequence(None) == []
        assert _coerce_sequence("   ") == []

    def test_bare_string_is_single_element(self) -> None:
        assert _coerce_sequence("a/test_x.py::test_a") == ["a/test_x.py::test_a"]


# --------------------------------------------------------------------------- #
# _polybench_node_id — colon form -> pytest node id
# --------------------------------------------------------------------------- #


class TestPolybenchNodeId:
    def test_module_level_none_class(self) -> None:
        assert (
            _polybench_node_id("pkg/test_x.py:None:test_a")
            == "pkg/test_x.py::test_a"
        )

    def test_class_based(self) -> None:
        assert (
            _polybench_node_id("pkg/test_x.py:Cls:test_a")
            == "pkg/test_x.py::Cls::test_a"
        )

    def test_two_segment_form(self) -> None:
        assert _polybench_node_id("pkg/test_x.py:test_a") == "pkg/test_x.py::test_a"

    def test_already_node_id_is_idempotent(self) -> None:
        assert (
            _polybench_node_id("pkg/test_x.py::Cls::test_a")
            == "pkg/test_x.py::Cls::test_a"
        )
        assert _polybench_node_id("pkg/test_x.py::test_a") == "pkg/test_x.py::test_a"

    def test_colon_free_passthrough(self) -> None:
        assert _polybench_node_id("just_a_name") == "just_a_name"

    def test_blank(self) -> None:
        assert _polybench_node_id("   ") == ""

    def test_list_end_to_end_drops_blanks(self) -> None:
        raw = "['a/test_x.py:None:test_a', '', 'a/test_x.py:C:test_b']"
        assert _polybench_test_list(raw) == [
            "a/test_x.py::test_a",
            "a/test_x.py::C::test_b",
        ]


# --------------------------------------------------------------------------- #
# Field surfacing: instance + to_task + loader
# --------------------------------------------------------------------------- #


class TestFieldSurfacing:
    def test_to_task_carries_lists(self) -> None:
        task = _instance(["t.py::f"], ["t.py::p1", "t.py::p2"]).to_task()
        assert task["fail_to_pass"] == ["t.py::f"]
        assert task["pass_to_pass"] == ["t.py::p1", "t.py::p2"]

    def test_to_task_defaults_empty(self) -> None:
        inst = SWEPolyBenchInstance(
            instance_id="x", repo="x/y", base_commit="c", problem_statement="p"
        )
        task = inst.to_task()
        assert task["fail_to_pass"] == []
        assert task["pass_to_pass"] == []

    def test_loader_parses_python_repr_columns(self, tmp_path: Path) -> None:
        # Mirror the real dataset: F2P / P2P as Python-repr colon strings
        # embedded verbatim inside an otherwise-JSON row.
        row = {
            "instance_id": "org__repo-1",
            "repo": "org/repo",
            "base_commit": "abc",
            "problem_statement": "fix",
            "language": "Python",
            "F2P": "['tests/test_x.py:None:test_a']",
            "P2P": "['tests/test_x.py:Cls:test_b', 'tests/test_x.py:Cls:test_c']",
        }
        path = tmp_path / "poly.jsonl"
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        bench = SWEPolyBench(dataset_path=str(path), split="full")
        inst = bench.instances[0]
        assert inst.fail_to_pass == ["tests/test_x.py::test_a"]
        assert inst.pass_to_pass == [
            "tests/test_x.py::Cls::test_b",
            "tests/test_x.py::Cls::test_c",
        ]
        assert bench.tasks()[0]["fail_to_pass"] == ["tests/test_x.py::test_a"]

    def test_loader_leaves_non_python_empty(self, tmp_path: Path) -> None:
        # A Java row's F2P/P2P are NOT pytest node ids — must not be surfaced.
        row = {
            "instance_id": "org__jrepo-1",
            "language": "java",
            "problem_statement": "fix",
            "F2P": "['SomeTest#testThing']",
            "P2P": "['OtherTest#testKept']",
        }
        path = tmp_path / "poly.json"
        path.write_text(json.dumps([row]), encoding="utf-8")
        bench = SWEPolyBench(dataset_path=str(path), split="full")
        inst = bench.instances[0]
        assert inst.fail_to_pass == []
        assert inst.pass_to_pass == []


# --------------------------------------------------------------------------- #
# evaluate — faithful pass/fail logic
# --------------------------------------------------------------------------- #


class TestFaithfulGrading:
    def test_all_green_passes(self) -> None:
        env = FakeEnv()
        task = _instance(["t.py::f1", "t.py::f2"], ["t.py::p1"]).to_task()
        assert SWEPolyBench().evaluate(task, "done", env) is True

    def test_fail_to_pass_still_failing_fails(self) -> None:
        env = FakeEnv(fail_ids=frozenset({"t.py::f1"}))
        task = _instance(["t.py::f1"], ["t.py::p1"]).to_task()
        assert SWEPolyBench().evaluate(task, "done", env) is False

    def test_pass_to_pass_regression_fails(self) -> None:
        env = FakeEnv(fail_ids=frozenset({"t.py::p1"}))
        task = _instance(["t.py::f1"], ["t.py::p1"]).to_task()
        assert SWEPolyBench().evaluate(task, "done", env) is False

    def test_exact_node_ids_reach_pytest(self) -> None:
        env = FakeEnv()
        f2p = ["a/test_x.py::test_sep[compound_model6-result6]"]
        p2p = ["a/test_x.py::Cls::test_coord", "a/test_x.py::test_cdot"]
        task = _instance(f2p, p2p).to_task()
        assert SWEPolyBench().evaluate(task, "done", env) is True
        joined = "\n".join(env.pytest_commands)
        for tid in f2p + p2p:
            assert tid in joined

    def test_parametrized_ids_are_shell_quoted(self) -> None:
        env = FakeEnv()
        pid = "a/test_x.py::test_sep[compound_model6-result6]"
        task = _instance([pid], []).to_task()
        SWEPolyBench().evaluate(task, "done", env)
        assert shlex.quote(pid) in env.pytest_commands[0]

    def test_test_patch_applied_before_named_tests(self) -> None:
        env = FakeEnv()
        task = _instance(["t.py::f1"], []).to_task()
        SWEPolyBench().evaluate(task, "done", env)
        assert env.written.get("_test_patch.diff") == task["test_patch"]
        assert env.commands[0] == "git apply _test_patch.diff"

    def test_failed_patch_apply_short_circuits(self) -> None:
        env = FakeEnv(apply_ok=False)
        task = _instance(["t.py::f1"], ["t.py::p1"]).to_task()
        assert SWEPolyBench().evaluate(task, "done", env) is False
        assert env.pytest_commands == []  # grading stopped at the failed apply

    def test_raw_python_repr_columns_are_graded(self) -> None:
        # A caller may hand evaluate() an unprocessed row (raw F2P/P2P).
        env = FakeEnv()
        task = {
            "instance_id": "org__repo-1",
            "language": "python",
            "test_patch": "",
            "F2P": "['tests/test_x.py:None:test_a']",
            "P2P": "[]",
        }
        assert SWEPolyBench().evaluate(task, "done", env) is True
        assert any("tests/test_x.py::test_a" in c for c in env.pytest_commands)

    def test_custom_pytest_cmd(self) -> None:
        env = FakeEnv()
        bench = SWEPolyBench(pytest_cmd="python -m pytest -q")
        task = _instance(["t.py::f1"], []).to_task()
        bench.evaluate(task, "done", env)
        assert env.pytest_commands[0].startswith("python -m pytest -q ")


class TestGradingChunking:
    def test_long_list_chunked_into_multiple_invocations(self) -> None:
        bench = SWEPolyBench(test_chunk_size=2)
        env = FakeEnv()
        f2p = [f"t.py::f{i}" for i in range(5)]
        task = _instance(f2p, []).to_task()  # empty P2P isolates the F2P count
        assert bench.evaluate(task, "done", env) is True
        assert len(env.pytest_commands) == 3  # ceil(5 / 2)
        joined = "\n".join(env.pytest_commands)
        for tid in f2p:
            assert tid in joined


# --------------------------------------------------------------------------- #
# Back-compat: blanket paths untouched for lists-absent / non-Python
# --------------------------------------------------------------------------- #


class TestFallbackBackCompat:
    def test_python_without_lists_uses_run_tests(self) -> None:
        env = _RunTestsEnv(tests_ok=True)
        task = _instance([], []).to_task()  # no named tests -> blanket
        assert SWEPolyBench().evaluate(task, "done", env) is True
        # No named-test pytest invocation happened (only the git-apply command).
        assert all("pytest" not in c for c in env.commands)

    def test_python_without_lists_blanket_failure_fails(self) -> None:
        env = _RunTestsEnv(tests_ok=False)
        task = _instance([], []).to_task()
        assert SWEPolyBench().evaluate(task, "done", env) is False

    def test_non_python_with_lists_ignores_named_grading(self) -> None:
        # Even if a JS task somehow carries fail_to_pass, evaluate must not run
        # pytest on it — it takes the language-command blanket path.
        env = FakeEnv()
        task = _instance(["spec.js::should_x"], [], language="javascript").to_task()
        # No run_tests on this env -> language command "npm test --silent".
        SWEPolyBench().evaluate(task, "done", env)
        assert env.pytest_commands == []
        assert any(c.startswith("npm test") for c in env.commands)


# --------------------------------------------------------------------------- #
# Reality check against the staged SWE-PolyBench dataset (skips if absent)
# --------------------------------------------------------------------------- #


_STAGED = Path.home() / ".chimera" / "datasets" / "swe-polybench" / "test.jsonl"


@pytest.mark.skipif(not _STAGED.exists(), reason="staged SWE-PolyBench absent")
class TestStagedDatasetReality:
    def test_python_instance_has_pytest_node_ids(self) -> None:
        bench = SWEPolyBench(
            dataset_path=str(_STAGED), split="full", language="python", limit=5
        )
        assert bench.tasks(), "expected at least one staged Python task"
        inst = bench.instances[0]
        # Real Python rows carry non-empty F2P that convert to pytest node ids.
        assert inst.fail_to_pass, "expected a non-empty fail_to_pass list"
        assert all("::" in tid for tid in inst.fail_to_pass)
        assert all("::" in tid for tid in inst.pass_to_pass)
