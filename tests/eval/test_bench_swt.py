from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass

import pytest

from chimera.eval.benchmarks.swt_bench import (
    SWT_BENCH_SYSTEM_HINT,
    SWTBench,
    SWTBenchInstance,
)


@dataclass
class FakeResult:
    success: bool


@dataclass
class FakeTestResult:
    all_passed: bool


class FakeEnv:
    """Records writes / commands; lets the test script the F2P sequence."""

    def __init__(
        self,
        apply_ok: bool = True,
        pre_pass: bool = False,
        post_pass: bool = True,
        gold_apply_ok: bool = True,
    ) -> None:
        self.writes: dict[str, str] = {}
        self.commands: list[str] = []
        self._apply_ok = apply_ok
        self._gold_apply_ok = gold_apply_ok
        self._pre_pass = pre_pass
        self._post_pass = post_pass
        self._test_calls = 0

    def write_file(self, path: str, content: str) -> None:
        self.writes[path] = content

    def run_command(self, cmd: str) -> FakeResult:
        self.commands.append(cmd)
        if "_gold_patch.diff" in cmd:
            return FakeResult(self._gold_apply_ok)
        if "git apply" in cmd:
            return FakeResult(self._apply_ok)
        # script execution: pre fails (nonzero), post passes
        if "_repro.py" in cmd:
            self._test_calls += 1
            ok = self._post_pass if self._test_calls > 1 else self._pre_pass
            return FakeResult(ok)
        return FakeResult(True)

    def run_tests(self) -> FakeTestResult:
        self._test_calls += 1
        if self._test_calls == 1:
            return FakeTestResult(self._pre_pass)
        return FakeTestResult(self._post_pass)


class TestSWTBench:
    def test_name(self):
        assert SWTBench().name() == "swt-bench"

    def test_default_mode_is_unit_test(self):
        assert SWTBench().mode == "unit_test"

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            SWTBench(mode="not-a-mode")

    def test_loads_from_json_array(self):
        items = [
            {
                "instance_id": "django__django-1",
                "repo": "django/django",
                "base_commit": "deadbeef",
                "problem_statement": "X crashes",
                "patch": "diff --git a/x.py b/x.py",
                "FAIL_TO_PASS": ["tests/test_x.py::test_crash"],
                "PASS_TO_PASS": ["tests/test_x.py::test_ok"],
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(items, f)
            f.flush()
            bench = SWTBench(dataset_path=f.name)
        tasks = bench.tasks()
        assert len(tasks) == 1
        assert tasks[0]["id"] == "django__django-1"
        assert tasks[0]["FAIL_TO_PASS"] == ["tests/test_x.py::test_crash"]
        assert tasks[0]["PASS_TO_PASS"] == ["tests/test_x.py::test_ok"]

    def test_loads_from_jsonl(self):
        items = [
            {"instance_id": "a", "repo": "r", "base_commit": "c1", "problem_statement": "p1"},
            {"instance_id": "b", "repo": "r", "base_commit": "c2", "problem_statement": "p2"},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for it in items:
                f.write(json.dumps(it) + "\n")
            f.flush()
            bench = SWTBench(dataset_path=f.name)
        assert len(bench.tasks()) == 2

    def test_limit(self):
        items = [{"instance_id": f"i{i}", "repo": "r", "base_commit": "c", "problem_statement": "p"} for i in range(5)]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(items, f)
            f.flush()
            bench = SWTBench(dataset_path=f.name, limit=2)
        assert len(bench.tasks()) == 2

    def test_tasks_cached(self):
        bench = SWTBench()
        bench.add_instance(SWTBenchInstance("x", "r", "c", "p"))
        assert bench.tasks() is bench.tasks()

    def test_evaluate_no_env_falls_back_to_nontrivial(self):
        bench = SWTBench()
        bench.add_instance(SWTBenchInstance("x", "r", "c", "p", patch="diff"))
        task = bench.tasks()[0]
        assert bench.evaluate(task, "def test_repro(): assert False  # long enough")
        assert not bench.evaluate(task, "")

    def test_evaluate_unit_test_f2p_success(self):
        bench = SWTBench(mode="unit_test")
        bench.add_instance(SWTBenchInstance("x", "r", "c", "p", patch="GOLD"))
        env = FakeEnv(apply_ok=True, pre_pass=False, post_pass=True)
        assert bench.evaluate(bench.tasks()[0], "AGENT_TESTS", env) is True
        assert env.writes["_agent_tests.diff"] == "AGENT_TESTS"
        assert env.writes["_gold_patch.diff"] == "GOLD"

    def test_evaluate_unit_test_pre_passes_means_no_repro(self):
        bench = SWTBench(mode="unit_test")
        bench.add_instance(SWTBenchInstance("x", "r", "c", "p", patch="GOLD"))
        env = FakeEnv(pre_pass=True, post_pass=True)
        # If tests pass on the buggy code, they don't reproduce the bug.
        assert bench.evaluate(bench.tasks()[0], "AGENT", env) is False

    def test_evaluate_unit_test_post_fails_means_regression(self):
        bench = SWTBench(mode="unit_test")
        bench.add_instance(SWTBenchInstance("x", "r", "c", "p", patch="GOLD"))
        env = FakeEnv(pre_pass=False, post_pass=False)
        assert bench.evaluate(bench.tasks()[0], "AGENT", env) is False

    def test_evaluate_unit_test_apply_failure(self):
        bench = SWTBench(mode="unit_test")
        bench.add_instance(SWTBenchInstance("x", "r", "c", "p", patch="GOLD"))
        env = FakeEnv(apply_ok=False)
        assert bench.evaluate(bench.tasks()[0], "AGENT", env) is False

    def test_evaluate_reproduction_success(self):
        bench = SWTBench(mode="reproduction")
        bench.add_instance(SWTBenchInstance("x", "r", "c", "p", patch="GOLD"))
        env = FakeEnv(pre_pass=False, post_pass=True)
        assert bench.evaluate(bench.tasks()[0], "print('repro')", env) is True

    def test_evaluate_no_gold_patch_fails(self):
        bench = SWTBench()
        bench.add_instance(SWTBenchInstance("x", "r", "c", "p"))  # no patch
        env = FakeEnv()
        assert bench.evaluate(bench.tasks()[0], "AGENT", env) is False

    def test_system_hint_present(self):
        assert "FAIL" in SWT_BENCH_SYSTEM_HINT
        assert "PASS" in SWT_BENCH_SYSTEM_HINT
