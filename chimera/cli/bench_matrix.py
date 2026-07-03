"""``chimera bench-matrix`` — the N-agents × M-benchmarks matrix CLI.

Generalizes ``bench-compare`` (one benchmark × N loops) to the full grid: any
set of agents from the runner registry crossed against any set of benchmarks,
under one shared budget / sandbox / grader, so the agent is the only free
variable across a benchmark column. See ``docs/specs/agent-benchmark-matrix.md``.

Example::

    chimera bench-matrix \\
        --agents react,plan-execute,codex \\
        --benchmarks human-eval,mbpp \\
        --model glm-5 \\
        --max-tool-calls 40 --max-cost 0.50 \\
        --registry ~/.chimera/agents/matrix.json \\
        --format markdown --output matrix.json

Agents resolve from the runner registry (built-ins: react, plan-execute,
reflexion, tree-of-thought, codex, kimi; extend with ``--registry`` JSON files
that add codenames / presets / external ACP / CLI / native-harness agents).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def add_bench_matrix_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the ``bench-matrix`` subcommand."""
    p = subparsers.add_parser(
        "bench-matrix",
        help="N agents x M benchmarks: one budget/sandbox/grader; the agent is the only variable",
    )
    p.add_argument(
        "--agents",
        default="react,plan-execute",
        help=(
            "Comma-separated agent ids from the runner registry (built-ins: "
            "react, plan-execute, reflexion, tree-of-thought, codex, kimi; add "
            "more via --registry)"
        ),
    )
    p.add_argument(
        "--benchmarks",
        required=True,
        help="Comma-separated benchmark registry names (e.g. human-eval,mbpp,swe-bench)",
    )
    p.add_argument(
        "--dataset",
        default=None,
        help="Dataset path applied to every benchmark (staged locally)",
    )
    p.add_argument("--limit", type=int, default=None, help="Per-benchmark task-count cap")
    p.add_argument("--model", default="glm-5", help="Model shared by every cell (default: glm-5)")
    p.add_argument(
        "--registry",
        default=None,
        help="Comma-separated JSON agent-registry files merged over the built-ins",
    )
    p.add_argument("--max-tool-calls", type=int, default=None, help="Budget: completed tool calls per task")
    p.add_argument("--max-llm-calls", type=int, default=None, help="Budget: provider calls per task")
    p.add_argument("--max-wall-clock", type=float, default=None, help="Budget: seconds per task")
    p.add_argument("--max-cost", type=float, default=None, help="Budget: dollars per task")
    p.add_argument(
        "--format",
        dest="fmt",
        choices=("terminal", "json", "markdown"),
        default="terminal",
        help="Stdout rendering",
    )
    p.add_argument("--output", default=None, help="Also write the full report JSON here")
    p.add_argument(
        "--env",
        dest="env_kind",
        choices=("local", "none"),
        default="local",
        help="Per-task environment: fresh temp-dir LocalEnvironment (default) or none",
    )


def _report_to_dict(report: Any) -> dict[str, Any]:
    """Render a MatrixReport as a JSON-safe dict."""
    import dataclasses

    return {
        "model": report.model,
        "cells": [dataclasses.asdict(c) for c in report.cells],
        "best_per_benchmark": report.best_per_benchmark(),
    }


def run_bench_matrix(args: argparse.Namespace) -> int:
    """Execute the bench-matrix command.

    Name validation (unknown agent / unknown benchmark) happens before any
    provider is constructed, so a typo fails fast without needing credentials.
    """
    from chimera.cli.main import _load_benchmark
    from chimera.core.budget import BudgetSpec
    from chimera.eval.matrix import run_matrix
    from chimera.eval.runners.registry import load_registry, resolve
    from chimera.providers.factory import create_provider

    agent_names = [a.strip() for a in args.agents.split(",") if a.strip()]
    bench_names = [b.strip() for b in args.benchmarks.split(",") if b.strip()]
    if not agent_names:
        print("Error: --agents is empty.", file=sys.stderr)
        return 1
    if not bench_names:
        print("Error: --benchmarks is empty.", file=sys.stderr)
        return 1

    registry_paths = (
        [p.strip() for p in args.registry.split(",") if p.strip()] if args.registry else None
    )
    registry = load_registry(registry_paths)

    # Validate names before touching a provider so typos fail fast + offline.
    try:
        for name in agent_names:
            if name not in registry:
                raise ValueError(
                    f"Unknown agent: {name}. Available: {', '.join(sorted(registry))}"
                )
        benchmarks = [
            _load_benchmark(n, dataset=args.dataset, limit=args.limit) for n in bench_names
        ]
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    provider = create_provider(model=args.model)
    try:
        runners = [resolve(registry[name], provider=provider) for name in agent_names]
    except (ValueError, NotImplementedError, ImportError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    budget = BudgetSpec(
        max_tool_calls=args.max_tool_calls,
        max_llm_calls=args.max_llm_calls,
        max_wall_clock_sec=args.max_wall_clock,
        max_cost_usd=args.max_cost,
    )

    env_factory: Any = None
    if args.env_kind == "local":
        import tempfile

        from chimera.env.local import LocalEnvironment

        def _local_env() -> LocalEnvironment:
            return LocalEnvironment(workdir=tempfile.mkdtemp(prefix="chimera-matrix-"))

        env_factory = _local_env

    print(
        f"Matrix: {len(runners)} agent(s) x {len(benchmarks)} benchmark(s) "
        f"with {args.model} (budget: {budget})...",
        file=sys.stderr,
    )
    report = run_matrix(
        runners, benchmarks, env_factory=env_factory, budget=budget, model=args.model
    )

    if args.fmt == "json":
        print(json.dumps(_report_to_dict(report), indent=2))
    else:
        print(report.summary())

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(_report_to_dict(report), f, indent=2)
        print(f"Report written to {args.output}", file=sys.stderr)

    return 0
