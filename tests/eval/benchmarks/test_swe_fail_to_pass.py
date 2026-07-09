"""Faithful FAIL_TO_PASS / PASS_TO_PASS grading — offline unit tests.

Covers the grading path added to :mod:`chimera.eval.benchmarks.swe_bench`:

* Parsing ``FAIL_TO_PASS`` / ``PASS_TO_PASS`` off a dataset row (the official
  JSON-string encoding, native lists, and the empty case) onto
  :class:`SWEBenchInstance` and through :meth:`SWEBenchInstance.to_task`.
* :meth:`SWEBench.evaluate` running exactly the named tests: the exact node ids
  reach pytest, chunking respects a size cap, and the pass/fail logic matches
  the official resolve criterion (every F2P passes AND every P2P passes).
* The conda-activation seam: auto-on for official per-instance images, off for
  plain envs, and the explicit-override knobs.
* Back-compat: instances without F2P/P2P still take the legacy blanket path.

A fake env captures every ``run_command`` so assertions are exact; no cloud,
container, or LLM is touched.
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from chimera.eval.benchmarks.swe_bench import (
    DEFAULT_CONDA_ACTIVATE,
    SWEBench,
    SWEBenchInstance,
    _as_test_list,
    _chunk_test_ids,
    _is_official_swe_image,
)
from chimera.types import CommandResult, TestResult

# An official per-instance evaluation image (drives auto conda activation).
_OFFICIAL_IMAGE = "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest"


# --------------------------------------------------------------------------- #
# Fake environment: records every run_command / write_file
# --------------------------------------------------------------------------- #


class FakeEnv:
    """Minimal env double capturing the grading command stream.

    Args:
        image: The env's image (``""`` => plain env, no auto conda).
        fail_ids: Node ids whose presence in a pytest command makes that run
            exit non-zero (simulates a still-failing / regressing test).
        apply_ok: Whether ``git apply`` of the test patch succeeds.
        run_tests_result: The :class:`TestResult` returned by ``run_tests`` (the
            blanket fallback path).
    """

    def __init__(
        self,
        image: str = "",
        *,
        fail_ids: frozenset[str] = frozenset(),
        apply_ok: bool = True,
        run_tests_result: TestResult | None = None,
    ) -> None:
        self.image = image
        self.commands: list[str] = []
        self.written: dict[str, str] = {}
        self._fail_ids = set(fail_ids)
        self._apply_ok = apply_ok
        self._run_tests_result = run_tests_result
        self.run_tests_called = False

    def write_file(self, path: str, content: str) -> None:
        self.written[path] = content

    def run_command(
        self, cmd: str, timeout: int | None = None, shell_name: str = "main"
    ) -> CommandResult:
        self.commands.append(cmd)
        if "pytest" in cmd:
            for fid in self._fail_ids:
                if fid in cmd:
                    return CommandResult(stdout="", stderr="", exit_code=1)
            return CommandResult(stdout="", stderr="", exit_code=0)
        # Anything else on this path is the ``git apply`` of the test patch.
        return CommandResult(stdout="", stderr="", exit_code=0 if self._apply_ok else 1)

    def run_tests(self) -> TestResult:
        self.run_tests_called = True
        if self._run_tests_result is not None:
            return self._run_tests_result
        return TestResult(passed=1, failed=0, errors=0, output="")

    # Helpers for assertions ------------------------------------------------- #

    @property
    def pytest_commands(self) -> list[str]:
        return [c for c in self.commands if "pytest" in c]


def _instance(
    fail_to_pass: list[str],
    pass_to_pass: list[str],
    *,
    test_patch: str = "diff --git a/t.py b/t.py",
) -> SWEBenchInstance:
    return SWEBenchInstance(
        instance_id="astropy__astropy-12907",
        repo="astropy/astropy",
        base_commit="abc123",
        problem_statement="fix the thing",
        test_patch=test_patch,
        fail_to_pass=fail_to_pass,
        pass_to_pass=pass_to_pass,
    )


# --------------------------------------------------------------------------- #
# _as_test_list — field normalisation
# --------------------------------------------------------------------------- #


class TestAsTestList:
    def test_json_string_list_is_parsed(self) -> None:
        # The official dataset encoding: a JSON string holding a list.
        raw = json.dumps(["a/test_x.py::test_a", "a/test_x.py::test_b"])
        assert _as_test_list(raw) == [
            "a/test_x.py::test_a",
            "a/test_x.py::test_b",
        ]

    def test_native_list_passthrough(self) -> None:
        assert _as_test_list(["t::a", "t::b"]) == ["t::a", "t::b"]

    def test_none_is_empty(self) -> None:
        assert _as_test_list(None) == []

    def test_blank_string_is_empty(self) -> None:
        assert _as_test_list("   ") == []
        assert _as_test_list("[]") == []

    def test_non_json_string_is_single_id(self) -> None:
        assert _as_test_list("t/test_x.py::test_a") == ["t/test_x.py::test_a"]

    def test_blank_entries_dropped(self) -> None:
        assert _as_test_list(["t::a", "", "  "]) == ["t::a"]

    def test_entries_coerced_to_str(self) -> None:
        assert _as_test_list([1, 2]) == ["1", "2"]


# --------------------------------------------------------------------------- #
# _chunk_test_ids — ARG_MAX-respecting chunking
# --------------------------------------------------------------------------- #


class TestChunkTestIds:
    def test_single_chunk_under_limit(self) -> None:
        ids = ["t::a", "t::b", "t::c"]
        assert _chunk_test_ids(ids, chunk_size=100) == [ids]

    def test_count_based_chunking(self) -> None:
        ids = [f"t::t{i}" for i in range(5)]
        chunks = _chunk_test_ids(ids, chunk_size=2)
        assert chunks == [["t::t0", "t::t1"], ["t::t2", "t::t3"], ["t::t4"]]

    def test_empty_input_yields_no_chunks(self) -> None:
        assert _chunk_test_ids([], chunk_size=10) == []

    def test_blank_ids_dropped_before_chunking(self) -> None:
        assert _chunk_test_ids(["t::a", "", "t::b"], chunk_size=100) == [
            ["t::a", "t::b"]
        ]

    def test_char_cap_forces_split(self) -> None:
        ids = ["x" * 40, "y" * 40, "z" * 40]
        # A tiny char budget forces one id per chunk despite a large count cap.
        chunks = _chunk_test_ids(ids, chunk_size=100, max_chars=50)
        assert chunks == [["x" * 40], ["y" * 40], ["z" * 40]]

    def test_chunk_size_clamped_to_one(self) -> None:
        ids = ["t::a", "t::b"]
        assert _chunk_test_ids(ids, chunk_size=0) == [["t::a"], ["t::b"]]

    def test_every_id_preserved_across_chunks(self) -> None:
        ids = [f"t::t{i}" for i in range(23)]
        chunks = _chunk_test_ids(ids, chunk_size=5)
        flat = [tid for chunk in chunks for tid in chunk]
        assert flat == ids


# --------------------------------------------------------------------------- #
# Field surfacing: instance + to_task + loader
# --------------------------------------------------------------------------- #


class TestFieldSurfacing:
    def test_to_task_carries_lists(self) -> None:
        task = _instance(["t::f"], ["t::p1", "t::p2"]).to_task()
        assert task["fail_to_pass"] == ["t::f"]
        assert task["pass_to_pass"] == ["t::p1", "t::p2"]

    def test_to_task_defaults_empty(self) -> None:
        inst = SWEBenchInstance(
            instance_id="x__y-1", repo="x/y", base_commit="c", problem_statement="p"
        )
        task = inst.to_task()
        assert task["fail_to_pass"] == []
        assert task["pass_to_pass"] == []

    def test_loader_parses_json_string_columns(self, tmp_path: Path) -> None:
        # Mirror the real dataset: FAIL_TO_PASS / PASS_TO_PASS as JSON strings.
        row = {
            "instance_id": "astropy__astropy-12907",
            "repo": "astropy/astropy",
            "base_commit": "abc",
            "problem_statement": "fix",
            "FAIL_TO_PASS": json.dumps(["a/test_x.py::test_a"]),
            "PASS_TO_PASS": json.dumps(
                ["a/test_x.py::test_b", "a/test_x.py::test_c"]
            ),
        }
        path = tmp_path / "swe.jsonl"
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")

        bench = SWEBench(dataset_path=str(path))
        inst = bench.instances[0]
        assert inst.fail_to_pass == ["a/test_x.py::test_a"]
        assert inst.pass_to_pass == [
            "a/test_x.py::test_b",
            "a/test_x.py::test_c",
        ]
        task = bench.tasks()[0]
        assert task["fail_to_pass"] == ["a/test_x.py::test_a"]

    def test_loader_accepts_lowercase_keys(self, tmp_path: Path) -> None:
        row = {
            "instance_id": "x__y-1",
            "problem_statement": "p",
            "fail_to_pass": ["t::a"],
            "pass_to_pass": ["t::b"],
        }
        path = tmp_path / "swe.json"
        path.write_text(json.dumps([row]), encoding="utf-8")
        bench = SWEBench(dataset_path=str(path))
        assert bench.instances[0].fail_to_pass == ["t::a"]
        assert bench.instances[0].pass_to_pass == ["t::b"]


# --------------------------------------------------------------------------- #
# evaluate — faithful pass/fail logic
# --------------------------------------------------------------------------- #


class TestFaithfulGrading:
    def test_all_green_passes(self) -> None:
        bench = SWEBench()
        env = FakeEnv(image=_OFFICIAL_IMAGE)
        task = _instance(["t::f1", "t::f2"], ["t::p1"]).to_task()
        assert bench.evaluate(task, "done", env) is True

    def test_fail_to_pass_still_failing_fails(self) -> None:
        bench = SWEBench()
        env = FakeEnv(image=_OFFICIAL_IMAGE, fail_ids=frozenset({"t::f1"}))
        task = _instance(["t::f1"], ["t::p1"]).to_task()
        assert bench.evaluate(task, "done", env) is False

    def test_pass_to_pass_regression_fails(self) -> None:
        bench = SWEBench()
        env = FakeEnv(image=_OFFICIAL_IMAGE, fail_ids=frozenset({"t::p1"}))
        task = _instance(["t::f1"], ["t::p1"]).to_task()
        assert bench.evaluate(task, "done", env) is False

    def test_exact_node_ids_reach_pytest(self) -> None:
        bench = SWEBench()
        env = FakeEnv(image=_OFFICIAL_IMAGE)
        f2p = ["a/test_x.py::test_sep[compound_model6-result6]"]
        p2p = ["a/test_x.py::test_coord_matrix", "a/test_x.py::test_cdot"]
        task = _instance(f2p, p2p).to_task()

        assert bench.evaluate(task, "done", env) is True
        joined = "\n".join(env.pytest_commands)
        for tid in f2p + p2p:
            assert tid in joined

    def test_parametrized_ids_are_shell_quoted(self) -> None:
        bench = SWEBench()
        env = FakeEnv(image=_OFFICIAL_IMAGE)
        pid = "a/test_x.py::test_sep[compound_model6-result6]"
        task = _instance([pid], []).to_task()
        bench.evaluate(task, "done", env)
        # The bracketed (glob-significant) id must be quoted for the shell.
        assert shlex.quote(pid) in env.pytest_commands[0]

    def test_test_patch_applied_before_named_tests(self) -> None:
        bench = SWEBench()
        env = FakeEnv(image=_OFFICIAL_IMAGE)
        task = _instance(["t::f1"], []).to_task()
        bench.evaluate(task, "done", env)
        assert env.written.get("_test_patch.diff") == task["test_patch"]
        assert env.commands[0] == "git apply _test_patch.diff"

    def test_failed_patch_apply_short_circuits(self) -> None:
        bench = SWEBench()
        env = FakeEnv(image=_OFFICIAL_IMAGE, apply_ok=False)
        task = _instance(["t::f1"], ["t::p1"]).to_task()
        assert bench.evaluate(task, "done", env) is False
        # No test ran — grading stopped at the failed apply.
        assert env.pytest_commands == []

    def test_no_env_is_false(self) -> None:
        bench = SWEBench()
        task = _instance(["t::f1"], []).to_task()
        assert bench.evaluate(task, "done", None) is False

    def test_raw_uppercase_task_keys_are_graded(self) -> None:
        # A caller may hand evaluate() an unprocessed dataset row.
        bench = SWEBench()
        env = FakeEnv(image=_OFFICIAL_IMAGE)
        task = {
            "instance_id": "x__y-1",
            "test_patch": "diff",
            "FAIL_TO_PASS": json.dumps(["t::f1"]),
            "PASS_TO_PASS": json.dumps(["t::p1"]),
        }
        assert bench.evaluate(task, "done", env) is True
        assert any("t::f1" in c for c in env.pytest_commands)


# --------------------------------------------------------------------------- #
# Chunking through evaluate
# --------------------------------------------------------------------------- #


class TestGradingChunking:
    def test_long_list_chunked_into_multiple_invocations(self) -> None:
        bench = SWEBench(test_chunk_size=2)
        env = FakeEnv(image=_OFFICIAL_IMAGE)
        f2p = [f"t::f{i}" for i in range(5)]
        task = _instance(f2p, []).to_task()  # empty P2P isolates the F2P count

        assert bench.evaluate(task, "done", env) is True
        # ceil(5 / 2) == 3 pytest invocations.
        assert len(env.pytest_commands) == 3
        joined = "\n".join(env.pytest_commands)
        for tid in f2p:
            assert tid in joined


# --------------------------------------------------------------------------- #
# Conda-activation seam
# --------------------------------------------------------------------------- #


class TestCondaPrefixSeam:
    def test_auto_on_for_official_image(self) -> None:
        bench = SWEBench()  # conda_prefix=None -> auto
        env = FakeEnv(image=_OFFICIAL_IMAGE)
        task = _instance(["t::f1"], []).to_task()
        bench.evaluate(task, "done", env)
        assert all(
            cmd.startswith(DEFAULT_CONDA_ACTIVATE) for cmd in env.pytest_commands
        )

    def test_auto_off_for_plain_env(self) -> None:
        bench = SWEBench()  # auto
        env = FakeEnv(image="")  # a LocalEnvironment-like env exposes no image
        task = _instance(["t::f1"], []).to_task()
        bench.evaluate(task, "done", env)
        assert env.pytest_commands  # tests still ran
        assert all("miniconda" not in cmd for cmd in env.pytest_commands)

    def test_explicit_empty_prefix_disables_on_official_image(self) -> None:
        bench = SWEBench(conda_prefix="")
        env = FakeEnv(image=_OFFICIAL_IMAGE)
        task = _instance(["t::f1"], []).to_task()
        bench.evaluate(task, "done", env)
        assert all("miniconda" not in cmd for cmd in env.pytest_commands)

    def test_explicit_custom_prefix_is_used_verbatim(self) -> None:
        bench = SWEBench(conda_prefix="source /custom/env; ")
        env = FakeEnv(image="")  # even on a plain env, an explicit prefix wins
        task = _instance(["t::f1"], []).to_task()
        bench.evaluate(task, "done", env)
        assert all(
            cmd.startswith("source /custom/env; ") for cmd in env.pytest_commands
        )

    def test_is_official_swe_image_marker(self) -> None:
        assert _is_official_swe_image(_OFFICIAL_IMAGE) is True
        assert _is_official_swe_image("python:3.11-slim") is False
        assert _is_official_swe_image("") is False


# --------------------------------------------------------------------------- #
# Back-compat: the legacy blanket path is untouched for plain instances
# --------------------------------------------------------------------------- #


class TestFallbackBackCompat:
    def test_no_f2p_uses_blanket_run_tests(self) -> None:
        bench = SWEBench()
        env = FakeEnv(image=_OFFICIAL_IMAGE)
        # No FAIL_TO_PASS / PASS_TO_PASS -> legacy path.
        task = _instance([], []).to_task()
        assert bench.evaluate(task, "done", env) is True
        assert env.run_tests_called is True
        assert env.pytest_commands == []  # no named-test invocations

    def test_blanket_failure_fails(self) -> None:
        bench = SWEBench()
        env = FakeEnv(
            image=_OFFICIAL_IMAGE,
            run_tests_result=TestResult(passed=1, failed=2, errors=0, output=""),
        )
        task = _instance([], []).to_task()
        assert bench.evaluate(task, "done", env) is False

    def test_patch_apply_failure_in_fallback_is_false(self) -> None:
        bench = SWEBench()
        env = FakeEnv(image=_OFFICIAL_IMAGE, apply_ok=False)
        task = _instance([], []).to_task()
        assert bench.evaluate(task, "done", env) is False
        assert env.run_tests_called is False


# --------------------------------------------------------------------------- #
# Reality check against the staged SWE-bench Lite dataset (skips if absent)
# --------------------------------------------------------------------------- #


_STAGED = Path.home() / ".chimera" / "datasets" / "swe-bench" / "lite-test.jsonl"


@pytest.mark.skipif(not _STAGED.exists(), reason="staged SWE-bench Lite absent")
class TestStagedDatasetReality:
    def test_first_instance_has_pytest_node_ids(self) -> None:
        bench = SWEBench(dataset_path=str(_STAGED), limit=3)
        inst = bench.instances[0]
        # Official rows carry non-empty JSON-string lists of pytest node ids.
        assert inst.fail_to_pass, "expected a non-empty FAIL_TO_PASS list"
        assert all("::" in tid for tid in inst.fail_to_pass)
        assert all("::" in tid for tid in inst.pass_to_pass)


class TestVacuousFallbackGuard:
    """A blanket run that executed ZERO tests must not grade as a pass.

    Live-proven hole (data/swe-modal-smoke.json): in an official image without
    conda activation, ``python -m pytest`` produced no parseable counters →
    ``TestResult(0, 0, 0)`` → ``all_passed`` True → a vacuous SWE "solve".
    """

    def _bench_and_task(self):
        from chimera.eval.benchmarks.swe_bench import SWEBench

        bench = SWEBench.__new__(SWEBench)
        bench._conda_prefix = ""
        bench._pytest_cmd = "python -m pytest"
        bench._test_chunk_size = 100
        bench._test_timeout = 1800
        # No fail_to_pass/pass_to_pass → evaluate() takes the blanket fallback.
        task = {"test_patch": "diff --git a/x b/x\n"}
        return bench, task

    def test_zero_tests_run_grades_false(self) -> None:
        bench, task = self._bench_and_task()
        env = FakeEnv(run_tests_result=TestResult(passed=0, failed=0, errors=0, output=""))
        assert bench.evaluate(task, "some output", env) is False

    def test_command_not_found_output_grades_false(self) -> None:
        bench, task = self._bench_and_task()
        env = FakeEnv(
            run_tests_result=TestResult(
                passed=0, failed=0, errors=0,
                output="/bin/sh: python: command not found",
            )
        )
        assert bench.evaluate(task, "some output", env) is False

    def test_real_passes_still_grade_true(self) -> None:
        bench, task = self._bench_and_task()
        env = FakeEnv(run_tests_result=TestResult(passed=3, failed=0, errors=0, output=""))
        assert bench.evaluate(task, "some output", env) is True
