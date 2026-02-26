# tests/test_bench_aimo.py
import json
from typing import Any

import pytest

from chimera.eval.benchmarks.aimo import AIMOBenchmark, extract_answer


class TestExtractAnswer:
    def test_extracts_last_integer(self):
        assert extract_answer("The answer is 12345") == 12345

    def test_extracts_from_multiple_numbers(self):
        assert extract_answer("I computed 3 + 4 = 7, so the answer is 12345") == 12345

    def test_extracts_from_boxed_latex(self):
        assert extract_answer("\\boxed{42567}") == 42567

    def test_extracts_answer_tag(self):
        assert extract_answer("ANSWER: 99999") == 99999

    def test_returns_none_for_no_number(self):
        assert extract_answer("I don't know") is None

    def test_handles_negative_gracefully(self):
        assert extract_answer("The result is -12345") == 12345

    def test_handles_multiline(self):
        text = "Step 1: compute 100\nStep 2: multiply by 3\nFinal answer: 54321"
        assert extract_answer(text) == 54321


class TestAIMOBenchmark:
    @pytest.fixture
    def problems_file(self, tmp_path):
        problems = [
            {"id": "p1", "problem": "Find x where x^2 = 144", "answer": 12},
            {"id": "p2", "problem": "What is 7! ?", "answer": 5040},
            {"id": "p3", "problem": "Compute gcd(48, 36)", "answer": 12},
        ]
        path = tmp_path / "problems.json"
        path.write_text(json.dumps(problems))
        return str(path)

    def test_name(self, problems_file):
        bench = AIMOBenchmark(problems_path=problems_file)
        assert bench.name() == "aimo3"

    def test_loads_tasks(self, problems_file):
        bench = AIMOBenchmark(problems_path=problems_file)
        tasks = bench.tasks()
        assert len(tasks) == 3
        assert tasks[0]["id"] == "p1"
        assert "prompt" in tasks[0]
        assert tasks[0]["answer"] == 12

    def test_evaluate_correct(self, problems_file):
        bench = AIMOBenchmark(problems_path=problems_file)
        task = bench.tasks()[1]  # answer is 5040
        assert bench.evaluate(task, "The factorial of 7 is 5040", None) is True

    def test_evaluate_incorrect(self, problems_file):
        bench = AIMOBenchmark(problems_path=problems_file)
        task = bench.tasks()[1]  # answer is 5040
        assert bench.evaluate(task, "The answer is 720", None) is False

    def test_evaluate_no_answer(self, problems_file):
        bench = AIMOBenchmark(problems_path=problems_file)
        task = bench.tasks()[0]
        assert bench.evaluate(task, "I cannot solve this", None) is False

    def test_limit(self, problems_file):
        bench = AIMOBenchmark(problems_path=problems_file, limit=2)
        assert len(bench.tasks()) == 2

    def test_empty_file(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("[]")
        bench = AIMOBenchmark(problems_path=str(path))
        assert bench.tasks() == []
