"""``chimera bench-fidelity`` — the replica-vs-real fidelity CLI.

Measures how faithfully a Chimera internal *replica* tracks the *real* external
agent it mirrors. One replica agent and one real agent are run on the **same**
benchmarks, **same** model, **same** budget, and **same** sandbox, so the only
free variable is replica-vs-real. This turns "we replicated agent X" from a
claim into a measured number: the pass-rate delta plus a coarse trajectory
(tool-call) divergence proxy. See ``docs/specs/agent-benchmark-matrix.md`` (the
"Signature experiment: replica vs. real" section).

Example::

    chimera bench-fidelity \\
        --replica codex --real react \\
        --benchmarks human-eval,mbpp \\
        --model glm-5 \\
        --max-tool-calls 40 --max-cost 0.50 \\
        --registry ~/.chimera/agents/matrix.json \\
        --format markdown --output fidelity.json

Both agents resolve from the runner registry (built-ins: react, plan-execute,
reflexion, tree-of-thought, codex, kimi; extend with ``--registry`` JSON files
that add codenames / presets / external ACP / CLI / native-harness agents).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def add_bench_fidelity_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the ``bench-fidelity`` subcommand."""
    p = subparsers.add_parser(
        "bench-fidelity",
        help=(
            "Replica-vs-real fidelity: one internal replica vs the real external "
            "agent on the same benchmarks/model/budget/sandbox"
        ),
    )
    p.add_argument(
        "--replica",
        required=True,
        help="Agent id (from the runner registry) of the internal Chimera replica",
    )
    p.add_argument(
        "--real",
        required=True,
        help="Agent id (from the runner registry) driving the real external agent",
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
    p.add_argument(
        "--model", default="glm-5", help="Model shared by both agents (default: glm-5)"
    )
    p.add_argument(
        "--registry",
        default=None,
        help="Comma-separated JSON agent-registry files merged over the built-ins",
    )
    p.add_argument(
        "--max-tool-calls", type=int, default=None, help="Budget: completed tool calls per task"
    )
    p.add_argument(
        "--max-llm-calls", type=int, default=None, help="Budget: provider calls per task"
    )
    p.add_argument("--max-wall-clock", type=float, default=None, help="Budget: seconds per task")
    p.add_argument("--max-cost", type=float, default=None, help="Budget: dollars per task")
    p.add_argument(
        "--format",
        dest="fmt",
        choices=("terminal", "json", "markdown"),
        default="markdown",
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


def run_bench_fidelity(args: argparse.Namespace) -> int:
    """Execute the bench-fidelity command.

    Validates the replica/real agent ids and every benchmark name *before* any
    provider is constructed, so a typo fails fast and offline (no credentials
    needed). Then resolves both runners against one shared provider and measures
    the replica against the real agent on each benchmark under one budget and
    one sandbox factory, via :func:`chimera.eval.fidelity.run_fidelity`.

    Args:
        args: Parsed arguments from :func:`add_bench_fidelity_parser`.

    Returns:
        Process exit code: ``0`` on success, ``1`` on a validation or
        runner-resolution error.
    """
    import dataclasses

    from chimera.cli.main import _load_benchmark
    from chimera.core.budget import BudgetSpec
    from chimera.eval.fidelity import render_markdown, run_fidelity
    from chimera.eval.runners.registry import load_registry, resolve
    from chimera.providers.factory import create_provider

    bench_names = [b.strip() for b in args.benchmarks.split(",") if b.strip()]
    if not bench_names:
        print("Error: --benchmarks is empty.", file=sys.stderr)
        return 1

    registry_paths = (
        [p.strip() for p in args.registry.split(",") if p.strip()] if args.registry else None
    )
    registry = load_registry(registry_paths)

    # Validate names before touching a provider so typos fail fast + offline.
    try:
        for role, agent_id in (("replica", args.replica), ("real", args.real)):
            if agent_id not in registry:
                raise ValueError(
                    f"Unknown {role} agent: {agent_id}. "
                    f"Available: {', '.join(sorted(registry))}"
                )
        benchmarks = [
            _load_benchmark(n, dataset=args.dataset, limit=args.limit) for n in bench_names
        ]
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    provider = create_provider(model=args.model)
    try:
        replica_runner = resolve(registry[args.replica], provider=provider)
        real_runner = resolve(registry[args.real], provider=provider)
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
            return LocalEnvironment(workdir=tempfile.mkdtemp(prefix="chimera-fidelity-"))

        env_factory = _local_env

    print(
        f"Fidelity: {args.replica} (replica) vs {args.real} (real) "
        f"x {len(benchmarks)} benchmark(s) with {args.model} (budget: {budget})...",
        file=sys.stderr,
    )
    results = [
        run_fidelity(
            replica_runner,
            real_runner,
            bench,
            env_factory=env_factory,
            budget=budget,
            model=args.model,
        )
        for bench in benchmarks
    ]

    payload = json.dumps([dataclasses.asdict(r) for r in results], indent=2)
    if args.fmt == "json":
        print(payload)
    else:
        print(render_markdown(results))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(payload)
        print(f"Report written to {args.output}", file=sys.stderr)

    return 0
