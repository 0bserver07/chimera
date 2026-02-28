"""Chimera CLI entry point.

Usage::

    chimera synthesize --spec "Build a calculator"
    chimera eval --benchmark swe-bench --dataset ./data.json --limit 10 --output results.json
    chimera bench --suite custom --tasks-dir ./tasks/ --output results.json
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from chimera.synthesize import synthesize as synthesize_fn


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="chimera",
        description="Chimera: AI-powered code synthesis framework",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ---- synthesize subcommand ----
    synth_parser = subparsers.add_parser(
        "synthesize",
        help="Synthesize code from a specification",
    )
    _add_synthesize_args(synth_parser)

    # ---- synth alias ----
    synth_alias = subparsers.add_parser(
        "synth",
        help="Alias for 'synthesize'",
    )
    _add_synthesize_args(synth_alias)

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
    eval_parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model to use (default: claude-sonnet-4-20250514)",
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
    bench_parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model to use (default: claude-sonnet-4-20250514)",
    )

    return parser


def _add_synthesize_args(parser: argparse.ArgumentParser) -> None:
    """Add common arguments for synthesize/synth subcommands."""
    parser.add_argument(
        "--spec",
        default=None,
        help="Specification text or path to spec file",
    )
    parser.add_argument(
        "--tests",
        default=None,
        help="Path to test directory",
    )
    parser.add_argument(
        "--output",
        default="./output",
        help="Output directory (default: ./output)",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model to use (default: claude-sonnet-4-20250514)",
    )
    parser.add_argument(
        "--provider",
        default="anthropic",
        help="Provider to use (default: anthropic)",
    )
    parser.add_argument(
        "--strategy",
        default="convergence",
        help="Strategy to use (default: convergence)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=50,
        help="Maximum iterations (default: 50)",
    )
    parser.add_argument(
        "--patience",
        type=int,
        default=5,
        help="Patience before stopping (default: 5)",
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="Maximum cost budget",
    )


# Backward-compatible alias for Phase 6-8 tests
create_parser = build_parser


_BENCHMARKS: dict[str, str] = {
    "human-eval": "chimera.eval.benchmarks.human_eval:HumanEval",
    "swe-bench": "chimera.eval.benchmarks.swe_bench:SWEBench",
    "aimo": "chimera.eval.benchmarks.aimo:AIMOBenchmark",
    "custom": "chimera.eval.benchmarks.custom:CustomBenchmark",
}


def _load_benchmark(
    name: str,
    dataset: str | None = None,
    limit: int | None = None,
    tasks_dir: str | None = None,
):
    """Instantiate a benchmark by name."""
    if name not in _BENCHMARKS:
        raise ValueError(f"Unknown benchmark: {name}. Available: {', '.join(_BENCHMARKS)}")
    module_path, class_name = _BENCHMARKS[name].rsplit(":", 1)
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    if name == "custom":
        return cls(tasks_dir=tasks_dir or dataset)
    kwargs: dict = {}
    if dataset:
        kwargs["dataset_path"] = dataset
    if limit:
        kwargs["limit"] = limit
    return cls(**kwargs)


def _result_to_dict(result) -> dict:
    """Convert EvalResult to a JSON-serializable dict."""
    import dataclasses
    return {
        "benchmark": result.benchmark,
        "total": result.total,
        "passed": result.passed,
        "pass_rate": result.pass_rate,
        "total_cost": result.total_cost,
        "results": [dataclasses.asdict(r) for r in result.results],
    }


def run_synthesize(args: argparse.Namespace) -> int:
    """Execute the synthesize command."""
    if not args.spec and not args.tests:
        print("Error: at least one of --spec or --tests is required.", file=sys.stderr)
        return 1

    spec_text = args.spec or "Make all tests pass."

    try:
        result = synthesize_fn(
            spec_text,
            tests=args.tests,
            model=args.model,
            workdir=args.output,
            max_iterations=args.max_iterations,
            patience=args.patience,
            max_cost=args.max_cost,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if result.converged:
        print(
            f"Synthesis converged in {result.iterations} iterations "
            f"(cost: ${result.total_cost:.4f})",
        )
        return 0
    else:
        print(
            f"Synthesis failed after {result.iterations} iterations "
            f"(best: {result.best_pass_rate:.0%}, cost: ${result.total_cost:.4f})",
            file=sys.stderr,
        )
        if result.failure_reason:
            print(f"Reason: {result.failure_reason}", file=sys.stderr)
        return 1


def run_eval(args: argparse.Namespace) -> int:
    """Execute the eval command."""
    from chimera.core.agent import Agent
    from chimera.core.tool_group import DEFAULT_TOOLS
    from chimera.eval.harness import Harness
    from chimera.providers.factory import create_provider

    try:
        benchmark = _load_benchmark(args.benchmark, dataset=args.dataset, limit=args.limit)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    provider = create_provider(model=args.model)
    agent = Agent(provider=provider, tools=list(DEFAULT_TOOLS))
    harness = Harness(benchmark, agent)

    print(f"Running {benchmark.name()} ({len(benchmark.tasks())} tasks) with {args.model}...", file=sys.stderr)
    result = harness.run()

    print(f"\n{result.benchmark}: {result.passed}/{result.total} passed ({result.pass_rate:.1%})", file=sys.stderr)
    print(f"Total cost: ${result.total_cost:.4f}", file=sys.stderr)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(_result_to_dict(result), f, indent=2)
        print(f"Results written to {args.output}", file=sys.stderr)

    return 0 if result.passed == result.total else 1


def run_bench(args: argparse.Namespace) -> int:
    """Execute the bench command."""
    from chimera.core.agent import Agent
    from chimera.core.tool_group import DEFAULT_TOOLS
    from chimera.eval.harness import Harness
    from chimera.providers.factory import create_provider

    try:
        benchmark = _load_benchmark("custom", tasks_dir=args.tasks_dir)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    provider = create_provider(model=args.model)
    agent = Agent(provider=provider, tools=list(DEFAULT_TOOLS))
    harness = Harness(benchmark, agent)

    print(f"Running {benchmark.name()} ({len(benchmark.tasks())} tasks) with {args.model}...", file=sys.stderr)
    result = harness.run()

    print(f"\n{result.benchmark}: {result.passed}/{result.total} passed ({result.pass_rate:.1%})", file=sys.stderr)
    print(f"Total cost: ${result.total_cost:.4f}", file=sys.stderr)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(_result_to_dict(result), f, indent=2)
        print(f"Results written to {args.output}", file=sys.stderr)

    return 0 if result.passed == result.total else 1


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command in ("synthesize", "synth"):
        return run_synthesize(args)
    elif args.command == "eval":
        return run_eval(args)
    elif args.command == "bench":
        return run_bench(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
