from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.eval.benchmarks.custom import CustomBenchmark


@dataclass
class FakeTestResult:
    all_passed: bool


class FakeEnv:
    def __init__(self, tests_pass: bool = True):
        self._tests_pass = tests_pass

    def run_tests(self) -> FakeTestResult:
        return FakeTestResult(all_passed=self._tests_pass)


class TestCustomBenchmark:
    def test_load_from_task_list(self):
        tasks = [
            {"id": "c1", "prompt": "Implement feature A"},
            {"id": "c2", "prompt": "Fix bug B"},
        ]
        bench = CustomBenchmark(tasks_list=tasks)
        assert bench.name() == "custom"
        loaded = bench.tasks()
        assert len(loaded) == 2
        assert loaded[0]["id"] == "c1"
        assert loaded[1]["prompt"] == "Fix bug B"

    def test_load_from_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create task files in sorted order
            for i, task in enumerate(
                [
                    {"id": "t1", "prompt": "Task one"},
                    {"id": "t2", "prompt": "Task two"},
                    {"id": "t3", "prompt": "Task three"},
                ]
            ):
                path = Path(tmpdir) / f"task_{i:03d}.json"
                path.write_text(json.dumps(task))

            bench = CustomBenchmark(tasks_dir=tmpdir)
            loaded = bench.tasks()

        assert len(loaded) == 3
        assert loaded[0]["id"] == "t1"
        assert loaded[2]["id"] == "t3"

    def test_load_from_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bench = CustomBenchmark(tasks_dir=tmpdir)
            loaded = bench.tasks()
            assert loaded == []

    def test_evaluate_with_passing_env(self):
        bench = CustomBenchmark()
        env = FakeEnv(tests_pass=True)
        assert bench.evaluate({"id": "t1"}, "output", env) is True

    def test_evaluate_with_failing_env(self):
        bench = CustomBenchmark()
        env = FakeEnv(tests_pass=False)
        assert bench.evaluate({"id": "t1"}, "output", env) is False

    def test_evaluate_without_env(self):
        bench = CustomBenchmark()
        assert bench.evaluate({"id": "t1"}, "output", None) is False

    def test_empty_task_list_default(self):
        bench = CustomBenchmark()
        assert bench.tasks() == []

    def test_directory_ignores_non_json_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "task_001.json").write_text(
                json.dumps({"id": "t1", "prompt": "Do it"})
            )
            (Path(tmpdir) / "readme.txt").write_text("Not a task")
            (Path(tmpdir) / "notes.md").write_text("# Notes")

            bench = CustomBenchmark(tasks_dir=tmpdir)
            loaded = bench.tasks()

        assert len(loaded) == 1
        assert loaded[0]["id"] == "t1"
