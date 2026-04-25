"""Tests for the CLI `eval` subcommand."""
from __future__ import annotations

import pytest

from chimera.cli.main import build_parser


class TestCliEval:
    def test_parse_eval_args(self):
        parser = build_parser()
        args = parser.parse_args([
            "eval",
            "--benchmark", "swe-bench",
            "--dataset", "./data.json",
            "--limit", "10",
            "--output", "results.json",
        ])
        assert args.command == "eval"
        assert args.benchmark == "swe-bench"
        assert args.dataset == "./data.json"
        assert args.limit == 10
        assert args.output == "results.json"

    def test_eval_help_text(self, capsys):
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["eval", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "benchmark" in captured.out.lower()

    def test_eval_missing_benchmark(self):
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["eval"])  # --benchmark is required
        assert exc_info.value.code != 0


class TestEvalWiring:
    def test_parse_model_flag(self):
        parser = build_parser()
        args = parser.parse_args([
            "eval", "--benchmark", "human-eval", "--model", "gpt-4o",
        ])
        assert args.model == "gpt-4o"

    def test_model_default(self):
        parser = build_parser()
        args = parser.parse_args(["eval", "--benchmark", "swe-bench"])
        assert args.model == "claude-sonnet-4-20250514"

    def test_load_benchmark_human_eval(self):
        from chimera.cli.main import _load_benchmark
        bench = _load_benchmark("human-eval")
        assert bench.name() == "human-eval"

    def test_load_benchmark_custom(self):
        from chimera.cli.main import _load_benchmark
        bench = _load_benchmark("custom", tasks_dir="/tmp")
        assert bench.name() == "custom"

    def test_load_benchmark_unknown(self):
        from chimera.cli.main import _load_benchmark
        with pytest.raises(ValueError, match="Unknown benchmark"):
            _load_benchmark("nonexistent")

    def test_result_to_dict(self):
        from chimera.cli.main import _result_to_dict
        from chimera.eval.harness import EvalResult, TaskEvalResult
        result = EvalResult(
            benchmark="test",
            total=2,
            passed=1,
            pass_rate=0.5,
            results=[
                TaskEvalResult(task_id="t1", passed=True, output="ok", cost=0.01, steps=3),
                TaskEvalResult(task_id="t2", passed=False, output="fail", cost=0.02, steps=5),
            ],
            total_cost=0.03,
        )
        d = _result_to_dict(result)
        assert d["benchmark"] == "test"
        assert d["passed"] == 1
        assert len(d["results"]) == 2
        assert d["results"][0]["task_id"] == "t1"
