"""``chimera bench-compare`` — the controlled comparative matrix CLI.

Runs the same task pool through N agent configurations under an
identical per-task budget (same model, same caps), producing the
``pass_rate x cost x steps x budget_hits`` matrix that
``docs/specs/comparative-bench-cli.md`` calls the headline deliverable.

Example::

    chimera bench-compare \\
        --agents react,plan-execute \\
        --benchmark harbor --dataset /path/to/deep-swe/tasks --limit 10 \\
        --model glm-5 \\
        --max-tool-calls 30 --max-wall-clock 600 --max-cost 5.00 \\
        --seed 0 --format markdown --output matrix.json

Agent configurations are loop types — every config shares the model,
tool set, and budget, so the loop architecture is the only variable.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

#: Loop-type registry: CLI name -> "module:Class".
LOOP_TYPES: dict[str, str] = {
    "react": "chimera.core.loop:ReAct",
    "plan-execute": "chimera.core.loops.plan_execute:PlanAndExecute",
    "reflexion": "chimera.core.loops.reflexion:Reflexion",
    "tree-of-thought": "chimera.core.loops.tree_of_thought:TreeOfThought",
}


def add_bench_compare_parser(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """Register the ``bench-compare`` subcommand."""
    p = subparsers.add_parser(
        "bench-compare",
        help="Controlled comparative matrix: same tasks, model, and budget; different agent loops",
    )
    p.add_argument(
        "--agents",
        default="react,plan-execute",
        help=f"Comma-separated loop types ({', '.join(LOOP_TYPES)})",
    )
    p.add_argument("--benchmark", required=True, help="Benchmark registry name (e.g. harbor, human-eval)")
    p.add_argument("--dataset", default=None, help="Benchmark dataset path")
    p.add_argument("--limit", type=int, default=None, help="Task-count cap")
    p.add_argument("--model", default="glm-5", help="Model shared by every config (default: glm-5)")
    p.add_argument("--max-steps", type=int, default=50, help="Loop-native step ceiling (safety net)")
    p.add_argument("--max-tool-calls", type=int, default=None, help="Budget: completed tool calls per task")
    p.add_argument("--max-llm-calls", type=int, default=None, help="Budget: provider calls per task")
    p.add_argument("--max-wall-clock", type=float, default=None, help="Budget: seconds per task")
    p.add_argument("--max-cost", type=float, default=None, help="Budget: dollars per task")
    p.add_argument("--seed", type=int, default=0, help="Task-sampling seed (recorded in the report)")
    p.add_argument(
        "--format",
        dest="fmt",
        choices=("terminal", "json", "markdown", "html"),
        default="terminal",
        help="Stdout rendering",
    )
    p.add_argument(
        "--output",
        default=None,
        help="Also write the full report here (.html gets the HTML report, anything else JSON)",
    )
    p.add_argument(
        "--emit-atif",
        default=None,
        metavar="DIR",
        help="Emit one ATIF v1.7 trajectory per (agent, task) under DIR",
    )
    p.add_argument(
        "--env",
        dest="env_kind",
        choices=("local", "none"),
        default="local",
        help="Per-task environment: a fresh temp-dir LocalEnvironment (default) or none",
    )


def _build_factories(agent_names: list[str], max_steps: int) -> dict[str, Any]:
    """Map loop-type names to ``(provider, loop_config) -> Agent`` factories.

    Raises:
        ValueError: If a name is not in :data:`LOOP_TYPES`.
    """
    import importlib

    factories: dict[str, Any] = {}
    for name in agent_names:
        if name not in LOOP_TYPES:
            raise ValueError(
                f"Unknown agent loop: {name}. Available: {', '.join(LOOP_TYPES)}"
            )
        module_path, class_name = LOOP_TYPES[name].rsplit(":", 1)
        loop_cls = getattr(importlib.import_module(module_path), class_name)

        def factory(provider: Any, loop_config: Any, _cls: Any = loop_cls) -> Any:
            from chimera.core.agent import Agent
            from chimera.core.tool_group import DEFAULT_TOOLS

            loop = _cls(max_steps=max_steps, config=loop_config)
            return Agent(provider=provider, tools=list(DEFAULT_TOOLS), loop=loop)

        factories[name] = factory
    return factories


def report_to_dict(report: Any) -> dict[str, Any]:
    """Render a CompareReport as a JSON-safe dict."""
    import dataclasses

    budget = report.budget
    return {
        "model": report.model,
        "task_pool": report.task_pool,
        "seed": report.seed,
        "budget": (
            dataclasses.asdict(budget)
            if dataclasses.is_dataclass(budget) and not isinstance(budget, type)
            else budget
        ),
        "configs": report.configs,
        "budget_hits": report.budget_hits,
        "budget_reasons": report.budget_reasons,
        "trajectory_paths": getattr(report, "trajectory_paths", {}),
        "results": {
            name: [dataclasses.asdict(r) for r in results]
            for name, results in report.results.items()
        },
    }


def report_to_markdown(report: Any) -> str:
    """Render a CompareReport as a paste-into-issue markdown matrix."""
    lines = [
        f"## Comparative matrix — `{report.model}` on `{report.task_pool}` (seed {report.seed})",
        "",
        "| Agent | Pass rate | Avg cost | Avg steps | Budget hits |",
        "|---|---|---|---|---|",
    ]
    for name in report.configs:
        results = report.results.get(name, [])
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        rate = passed / total if total else 0.0
        avg_cost = sum(r.cost for r in results) / total if total else 0.0
        avg_steps = sum(r.steps for r in results) / total if total else 0.0
        hits = report.budget_hits.get(name, 0)
        lines.append(
            f"| {name} | {rate:.1%} ({passed}/{total}) | ${avg_cost:.4f} "
            f"| {avg_steps:.1f} | {hits}/{total} |"
        )
    return "\n".join(lines)


def report_to_html(report: Any) -> str:
    """Render a CompareReport as a standalone HTML page.

    Sortable matrix (click a column header) plus a per-config, per-task
    drill-down. No external assets — safe to attach to an issue or
    email.
    """
    import html as _html

    def esc(value: Any) -> str:
        return _html.escape(str(value))

    matrix_rows = []
    for name in report.configs:
        results = report.results.get(name, [])
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        rate = passed / total if total else 0.0
        avg_cost = sum(r.cost for r in results) / total if total else 0.0
        avg_steps = sum(r.steps for r in results) / total if total else 0.0
        hits = report.budget_hits.get(name, 0)
        matrix_rows.append(
            f"<tr><td>{esc(name)}</td>"
            f'<td data-sort="{rate:.4f}">{rate:.1%} ({passed}/{total})</td>'
            f'<td data-sort="{avg_cost:.6f}">${avg_cost:.4f}</td>'
            f'<td data-sort="{avg_steps:.2f}">{avg_steps:.1f}</td>'
            f'<td data-sort="{hits}">{hits}/{total}</td></tr>'
        )

    drilldowns = []
    for name in report.configs:
        rows = "".join(
            f"<tr><td>{esc(r.problem_id)}</td>"
            f"<td>{'pass' if r.passed else 'fail'}</td>"
            f"<td>{r.steps}</td><td>${r.cost:.4f}</td></tr>"
            for r in report.results.get(name, [])
        )
        reasons = ", ".join(report.budget_reasons.get(name, [])) or "none"
        drilldowns.append(
            f"<details><summary>{esc(name)} — per-task results</summary>"
            f"<p>Budget hits: {esc(reasons)}</p>"
            "<table><thead><tr><th>Task</th><th>Result</th><th>Steps</th>"
            f"<th>Cost</th></tr></thead><tbody>{rows}</tbody></table></details>"
        )

    sort_js = (
        "document.querySelectorAll('th').forEach(function(th){"
        "th.style.cursor='pointer';"
        "th.addEventListener('click',function(){"
        "var table=th.closest('table'),tbody=table.querySelector('tbody'),"
        "idx=Array.prototype.indexOf.call(th.parentNode.children,th),"
        "dir=th.dataset.dir==='asc'?-1:1;th.dataset.dir=dir===1?'asc':'desc';"
        "Array.from(tbody.rows).sort(function(a,b){"
        "var x=a.cells[idx].dataset.sort||a.cells[idx].textContent,"
        "y=b.cells[idx].dataset.sort||b.cells[idx].textContent,"
        "nx=parseFloat(x),ny=parseFloat(y);"
        "return (isNaN(nx)||isNaN(ny)?x.localeCompare(y):nx-ny)*dir;"
        "}).forEach(function(r){tbody.appendChild(r);});});});"
    )

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Comparative matrix — {esc(report.model)}</title>"
        "<style>body{font:14px/1.5 system-ui;margin:2rem;max-width:60rem}"
        "table{border-collapse:collapse;margin:1rem 0}"
        "td,th{border:1px solid #ccc;padding:.35rem .6rem;text-align:left}"
        "th{background:#f3f3f3}details{margin:.75rem 0}</style></head><body>"
        f"<h1>Controlled comparative matrix</h1>"
        f"<p>model <code>{esc(report.model)}</code> · task pool "
        f"<code>{esc(report.task_pool)}</code> · seed {esc(report.seed)} · "
        f"budget <code>{esc(report.budget)}</code></p>"
        "<table id='matrix'><thead><tr><th>Agent</th><th>Pass rate</th>"
        "<th>Avg cost</th><th>Avg steps</th><th>Budget hits</th></tr></thead>"
        f"<tbody>{''.join(matrix_rows)}</tbody></table>"
        f"{''.join(drilldowns)}"
        f"<script>{sort_js}</script></body></html>"
    )


def run_bench_compare(args: argparse.Namespace) -> int:
    """Execute the bench-compare command."""
    from chimera.cli.main import _load_benchmark
    from chimera.core.budget import BudgetSpec
    from chimera.eval.comparative import ComparativeEval
    from chimera.providers.factory import create_provider

    agent_names = [a.strip() for a in args.agents.split(",") if a.strip()]
    try:
        factories = _build_factories(agent_names, args.max_steps)
        benchmark = _load_benchmark(args.benchmark, dataset=args.dataset, limit=args.limit)
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    problems = benchmark.tasks()
    if not problems:
        print("Error: benchmark produced no tasks.", file=sys.stderr)
        return 1

    budget = BudgetSpec(
        max_tool_calls=args.max_tool_calls,
        max_llm_calls=args.max_llm_calls,
        max_wall_clock_sec=args.max_wall_clock,
        max_cost_usd=args.max_cost,
    )
    task_pool = f"{args.benchmark}:{args.dataset or 'builtin'}?n={len(problems)}"

    env_factory: Any = None
    if args.env_kind == "local":
        import tempfile

        from chimera.env.local import LocalEnvironment

        def _local_env() -> LocalEnvironment:
            return LocalEnvironment(workdir=tempfile.mkdtemp(prefix="chimera-compare-"))

        env_factory = _local_env

    provider = create_provider(model=args.model)
    comp = ComparativeEval(provider, problems, env_factory=env_factory)
    for name, factory in factories.items():
        comp.add_config(name, factory)

    print(
        f"Comparing {len(factories)} agent loop(s) on {len(problems)} task(s) "
        f"with {args.model} (budget: {budget})...",
        file=sys.stderr,
    )
    report = comp.run_with_budget(
        budget,
        model=args.model,
        task_pool=task_pool,
        seed=args.seed,
        evaluator=lambda problem, output, env: benchmark.evaluate(problem, output, env),
        atif_dir=args.emit_atif,
    )
    if args.emit_atif:
        n_traj = sum(len(v) for v in report.trajectory_paths.values())
        print(f"Emitted {n_traj} ATIF trajectories under {args.emit_atif}", file=sys.stderr)

    if args.fmt == "json":
        print(json.dumps(report_to_dict(report), indent=2))
    elif args.fmt == "markdown":
        print(report_to_markdown(report))
    elif args.fmt == "html":
        print(report_to_html(report))
    else:
        print(report.summary())
        print(f"Best config: {report.best_config()}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            if args.output.endswith(".html"):
                f.write(report_to_html(report))
            else:
                json.dump(report_to_dict(report), f, indent=2)
        print(f"Report written to {args.output}", file=sys.stderr)

    return 0
