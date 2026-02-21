from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.eval.benchmarks.swe_bench import SWEBench


@dataclass
class FakeTestResult:
    all_passed: bool


class FakeEnv:
    def __init__(self, tests_pass: bool = True):
        self._tests_pass = tests_pass

    def run_tests(self) -> FakeTestResult:
        return FakeTestResult(all_passed=self._tests_pass)


class TestSWEBench:
    def test_loads_from_json_file(self):
        tasks = [
            {"id": "django__django-12345", "prompt": "Fix the bug in models.py"},
            {"id": "django__django-67890", "prompt": "Add migration support"},
        ]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(tasks, f)
            f.flush()
            bench = SWEBench(dataset_path=f.name)
            loaded = bench.tasks()

        assert len(loaded) == 2
        assert loaded[0]["id"] == "django__django-12345"
        assert loaded[1]["prompt"] == "Add migration support"

    def test_loads_from_json_with_tasks_key(self):
        data = {
            "metadata": {"version": "1.0"},
            "tasks": [
                {"id": "t1", "prompt": "Fix it"},
            ],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(data, f)
            f.flush()
            bench = SWEBench(dataset_path=f.name)
            loaded = bench.tasks()

        assert len(loaded) == 1
        assert loaded[0]["id"] == "t1"

    def test_limit_tasks(self):
        tasks = [{"id": f"t{i}", "prompt": f"task {i}"} for i in range(10)]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(tasks, f)
            f.flush()
            bench = SWEBench(dataset_path=f.name, limit=3)
            loaded = bench.tasks()

        assert len(loaded) == 3

    def test_evaluate_with_passing_env(self):
        bench = SWEBench()
        env = FakeEnv(tests_pass=True)
        assert bench.evaluate({"id": "t1"}, "some output", env) is True

    def test_evaluate_with_failing_env(self):
        bench = SWEBench()
        env = FakeEnv(tests_pass=False)
        assert bench.evaluate({"id": "t1"}, "some output", env) is False

    def test_evaluate_without_env(self):
        bench = SWEBench()
        assert bench.evaluate({"id": "t1"}, "output", None) is False

    def test_empty_dataset(self):
        bench = SWEBench()  # No dataset_path
        assert bench.tasks() == []

    def test_name(self):
        bench = SWEBench()
        assert bench.name() == "swe-bench"

    def test_tasks_cached(self):
        """Tasks are loaded only once and then cached."""
        tasks = [{"id": "t1", "prompt": "a"}]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(tasks, f)
            f.flush()
            bench = SWEBench(dataset_path=f.name)
            first = bench.tasks()
            second = bench.tasks()
            assert first is second  # Same object, not reloaded
