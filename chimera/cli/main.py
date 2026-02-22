"""Chimera CLI entry point.

Usage::

    chimera eval --benchmark swe-bench --dataset ./data.json --limit 10 --output results.json
    chimera bench --suite custom --tasks-dir ./tasks/ --output results.json
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="chimera",
        description="Chimera: AI-powered code synthesis framework",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ---- eval subcommand ----
    eval_parser = subparsers.add_parser(
        "eval",
        help="Evaluate an agent against a benchmark",
    )
    eval_parser.add_argument(
        "--benchmark",
        required=True,
        help="Benchmark to evaluate against (e.g. swe-bench, human-eval)",
    )
    eval_parser.add_argument(
        "--dataset",
        default=None,
        help="Path to dataset file (JSON)",
    )
    eval_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of tasks to evaluate",
    )
    eval_parser.add_argument(
        "--output",
        default=None,
        help="Path to write results JSON",
    )

    # ---- bench subcommand ----
    bench_parser = subparsers.add_parser(
        "bench",
        help="Run a benchmark suite",
    )
    bench_parser.add_argument(
        "--suite",
        required=True,
        help="Benchmark suite to run (e.g. custom, full)",
    )
    bench_parser.add_argument(
        "--tasks-dir",
        default=None,
        help="Directory containing task definitions",
    )
    bench_parser.add_argument(
        "--output",
        default=None,
        help="Path to write results JSON",
    )

    return parser


def run_eval(args: argparse.Namespace) -> int:
    """Execute the eval command."""
    print(f"Running evaluation: benchmark={args.benchmark}", file=sys.stderr)
    if args.dataset:
        print(f"  dataset: {args.dataset}", file=sys.stderr)
    if args.limit:
        print(f"  limit: {args.limit}", file=sys.stderr)
    if args.output:
        print(f"  output: {args.output}", file=sys.stderr)
    # Actual evaluation logic would go here
    return 0


def run_bench(args: argparse.Namespace) -> int:
    """Execute the bench command."""
    print(f"Running benchmark suite: {args.suite}", file=sys.stderr)
    if args.tasks_dir:
        print(f"  tasks-dir: {args.tasks_dir}", file=sys.stderr)
    if args.output:
        print(f"  output: {args.output}", file=sys.stderr)
    # Actual bench logic would go here
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "eval":
        return run_eval(args)
    elif args.command == "bench":
        return run_bench(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
