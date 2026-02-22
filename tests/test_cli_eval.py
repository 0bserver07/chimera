"""Tests for the CLI `eval` subcommand."""
from __future__ import annotations

import pytest

from chimera.cli.main import build_parser, main


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
