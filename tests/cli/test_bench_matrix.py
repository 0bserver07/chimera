"""Plumbing tests for the bench-matrix CLI (no LLM / provider required)."""

from __future__ import annotations

import argparse

from chimera.cli.bench_matrix import add_bench_matrix_parser, run_bench_matrix
from chimera.eval.runners import CliTemplateRunner
from chimera.eval.runners.registry import AgentSpec, resolve


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    add_bench_matrix_parser(subparsers)
    return parser.parse_args(argv)


def test_parser_registers_and_parses() -> None:
    ns = _parse(
        ["bench-matrix", "--agents", "react,codex", "--benchmarks", "human-eval,mbpp"]
    )
    assert ns.command == "bench-matrix"
    assert ns.agents == "react,codex"
    assert ns.benchmarks == "human-eval,mbpp"
    assert ns.model == "glm-5"  # default


def test_unknown_agent_fails_fast_without_provider(capsys) -> None:
    ns = _parse(["bench-matrix", "--agents", "no-such-agent", "--benchmarks", "human-eval"])
    rc = run_bench_matrix(ns)
    assert rc == 1
    assert "Unknown agent" in capsys.readouterr().err


def test_unknown_benchmark_fails_fast_without_provider(capsys) -> None:
    ns = _parse(["bench-matrix", "--agents", "react", "--benchmarks", "no-such-bench"])
    rc = run_bench_matrix(ns)
    assert rc == 1
    assert "Unknown benchmark" in capsys.readouterr().err


def test_resolve_constructs_real_external_runner() -> None:
    # Closes the coverage gap flagged during wave 1: resolve() actually builds an
    # external runner now that the runner modules are integrated.
    spec = AgentSpec(id="local-cli", kind="cli-template", cmd="echo {prompt}")
    runner = resolve(spec)
    assert isinstance(runner, CliTemplateRunner)
    assert runner.id == "local-cli"
