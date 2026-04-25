"""Tests for the CLI `bench` subcommand."""
from __future__ import annotations

import pytest

from chimera.cli.main import build_parser


class TestCliBench:
    def test_parse_bench_args(self):
        parser = build_parser()
        args = parser.parse_args([
            "bench",
            "--suite", "custom",
            "--tasks-dir", "./tasks/",
            "--output", "results.json",
        ])
        assert args.command == "bench"
        assert args.suite == "custom"
        assert args.tasks_dir == "./tasks/"
        assert args.output == "results.json"

    def test_bench_help_text(self, capsys):
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["bench", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "suite" in captured.out.lower()

    def test_bench_missing_suite(self):
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["bench"])  # --suite is required
        assert exc_info.value.code != 0


class TestBenchWiring:
    def test_parse_model_flag(self):
        parser = build_parser()
        args = parser.parse_args([
            "bench", "--suite", "custom", "--model", "gpt-4o",
        ])
        assert args.model == "gpt-4o"

    def test_model_default(self):
        parser = build_parser()
        args = parser.parse_args(["bench", "--suite", "custom"])
        assert args.model == "claude-sonnet-4-20250514"
