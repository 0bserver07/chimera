"""Plumbing tests for the bench-fidelity CLI (no LLM / provider required)."""

from __future__ import annotations

import argparse

from chimera.cli.bench_fidelity import add_bench_fidelity_parser, run_bench_fidelity


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    add_bench_fidelity_parser(subparsers)
    return parser.parse_args(argv)


def test_parser_registers_and_parses() -> None:
    ns = _parse(
        [
            "bench-fidelity",
            "--replica",
            "codex",
            "--real",
            "react",
            "--benchmarks",
            "human-eval,mbpp",
        ]
    )
    assert ns.command == "bench-fidelity"
    assert ns.replica == "codex"
    assert ns.real == "react"
    assert ns.benchmarks == "human-eval,mbpp"
    assert ns.model == "glm-5"  # default
    assert ns.fmt == "markdown"  # default
    assert ns.env_kind == "local"  # default


def test_unknown_replica_fails_fast_without_provider(capsys) -> None:
    ns = _parse(
        [
            "bench-fidelity",
            "--replica",
            "no-such-agent",
            "--real",
            "react",
            "--benchmarks",
            "human-eval",
        ]
    )
    rc = run_bench_fidelity(ns)
    assert rc == 1
    assert "Unknown replica agent" in capsys.readouterr().err


def test_unknown_real_fails_fast_without_provider(capsys) -> None:
    ns = _parse(
        [
            "bench-fidelity",
            "--replica",
            "react",
            "--real",
            "no-such-agent",
            "--benchmarks",
            "human-eval",
        ]
    )
    rc = run_bench_fidelity(ns)
    assert rc == 1
    assert "Unknown real agent" in capsys.readouterr().err


def test_unknown_benchmark_fails_fast_without_provider(capsys) -> None:
    ns = _parse(
        [
            "bench-fidelity",
            "--replica",
            "react",
            "--real",
            "codex",
            "--benchmarks",
            "no-such-bench",
        ]
    )
    rc = run_bench_fidelity(ns)
    assert rc == 1
    assert "Unknown benchmark" in capsys.readouterr().err
