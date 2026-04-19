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
from typing import Any, Sequence

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

    # ---- code subcommand ----
    code_parser = subparsers.add_parser(
        "code",
        help="Interactive coding agent REPL",
    )
    code_parser.add_argument(
        "--model",
        default=None,
        help="Model to use (default: ANTHROPIC_MODEL env var, or claude-sonnet-4-20250514)",
    )
    code_parser.add_argument(
        "--workdir",
        default=".",
        help="Working directory (default: current directory)",
    )
    code_parser.add_argument(
        "--max-steps",
        type=int,
        default=50,
        help="Maximum agent steps per turn (default: 50)",
    )
    code_parser.add_argument(
        "--mode",
        choices=["interactive", "rpc", "json"],
        default="interactive",
        help="Output mode (default: interactive)",
    )
    code_parser.add_argument(
        "--models",
        default="",
        help="Comma-separated list of models to cycle through (e.g. glm-5,claude-sonnet-4)",
    )
    code_parser.add_argument(
        "--preset",
        default=None,
        choices=["claude_code", "codex", "minimal", "explore"],
        help="Agent preset — uses the new CodingAgent stack (default: legacy stack)",
    )
    code_parser.add_argument(
        "-p", "--print",
        dest="print_mode",
        default=None,
        help="Non-interactive: run a single task and print the result",
    )

    # ---- review subcommand ----
    review_parser = subparsers.add_parser(
        "review",
        help="Run AI code review on a diff",
    )
    review_parser.add_argument(
        "--diff",
        required=True,
        help="Path to diff file",
    )
    review_parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model to use (default: claude-sonnet-4-20250514)",
    )
    review_parser.add_argument(
        "--max-rounds",
        type=int,
        default=3,
        help="Maximum review rounds (default: 3)",
    )

    # ---- ci-fix subcommand ----
    cifix_parser = subparsers.add_parser(
        "ci-fix",
        help="Diagnose and fix CI failures",
    )
    cifix_parser.add_argument(
        "--log",
        required=True,
        help="Path to CI log file",
    )
    cifix_parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model to use (default: claude-sonnet-4-20250514)",
    )
    cifix_parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum fix attempts (default: 3)",
    )

    # ---- research subcommand ----
    research_parser = subparsers.add_parser(
        "research",
        help="Research a question using AI",
    )
    research_parser.add_argument(
        "--question",
        required=True,
        help="Research question",
    )
    research_parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model to use (default: claude-sonnet-4-20250514)",
    )
    research_parser.add_argument(
        "--workdir",
        default=".",
        help="Working directory (default: current directory)",
    )

    # ---- docs subcommand ----
    docs_parser = subparsers.add_parser(
        "docs",
        help="Generate API documentation from source code",
    )
    docs_parser.add_argument(
        "--source",
        required=True,
        help="Source directory to scan",
    )
    docs_parser.add_argument(
        "--output",
        default="docs/api",
        help="Output directory (default: docs/api)",
    )

    # ---- testgen subcommand ----
    testgen_parser = subparsers.add_parser(
        "testgen",
        help="Generate test case skeletons from source code",
    )
    testgen_parser.add_argument(
        "--source",
        required=True,
        help="Source directory to scan",
    )
    testgen_parser.add_argument(
        "--output",
        default="tests/generated",
        help="Output directory (default: tests/generated)",
    )

    # ---- migrate subcommand ----
    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Apply migration rules to source files",
    )
    migrate_parser.add_argument(
        "--source",
        required=True,
        help="Source directory to migrate",
    )
    migrate_parser.add_argument(
        "--preset",
        required=True,
        help="Migration preset (e.g. python2-to-3, commonjs-to-esm)",
    )

    # ---- fs subcommand ----
    from chimera.cli import fs as _fs_cli
    _fs_cli.register(subparsers)

    # ---- plugins subcommand ----
    plugins_parser = subparsers.add_parser(
        "plugins",
        help="Manage plugins (search, install, uninstall)",
    )
    plugins_parser.add_argument(
        "action",
        choices=["search", "install", "uninstall"],
        help="Plugin action",
    )
    plugins_parser.add_argument(
        "query",
        nargs="?",
        default="",
        help="Plugin name or search query",
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
    kwargs: dict[str, Any] = {}
    if dataset:
        kwargs["dataset_path"] = dataset
    if limit:
        kwargs["limit"] = limit
    return cls(**kwargs)


def _result_to_dict(result: Any) -> dict[str, Any]:
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
    from chimera.env.local import LocalEnvironment
    from chimera.eval.harness import Harness
    from chimera.providers.factory import create_provider

    try:
        benchmark = _load_benchmark(args.benchmark, dataset=args.dataset, limit=args.limit)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    provider = create_provider(model=args.model)
    agent = Agent(provider=provider, tools=list(DEFAULT_TOOLS))

    def _eval_env_factory() -> LocalEnvironment:
        import tempfile
        d = tempfile.mkdtemp(prefix="chimera-eval-")
        e = LocalEnvironment(workdir=d)
        e.setup()
        return e

    harness = Harness(benchmark, agent, env_factory=_eval_env_factory)

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


def run_review(args: argparse.Namespace) -> int:
    """Execute the review command."""
    from chimera.core.agent import Agent
    from chimera.core.prompt import Prompt
    from chimera.env.local import LocalEnvironment
    from chimera.providers.factory import create_provider
    from chimera.review.orchestrator import ReviewOrchestrator

    try:
        with open(args.diff) as f:
            diff = f.read()
    except FileNotFoundError:
        print(f"Error: diff file not found: {args.diff}", file=sys.stderr)
        return 1

    provider = create_provider(model=args.model)
    reviewer = Agent(provider=provider, prompt=Prompt("You are a code reviewer. Review diffs for bugs, style issues, and improvements."))
    author = Agent(provider=provider)
    env = LocalEnvironment(workdir=".")

    orchestrator = ReviewOrchestrator(max_rounds=args.max_rounds)
    approved = orchestrator.run(diff, reviewer, author, env)

    if approved:
        print("Review: APPROVED")
        return 0
    else:
        print(f"Review: NOT APPROVED after {orchestrator.current_round} rounds")
        return 1


def run_ci_fix(args: argparse.Namespace) -> int:
    """Execute the ci-fix command."""
    from chimera.ci.fix_workflow import CIFixWorkflow
    from chimera.core.agent import Agent
    from chimera.env.local import LocalEnvironment
    from chimera.providers.factory import create_provider

    try:
        with open(args.log) as f:
            log = f.read()
    except FileNotFoundError:
        print(f"Error: log file not found: {args.log}", file=sys.stderr)
        return 1

    provider = create_provider(model=args.model)
    agent = Agent(provider=provider)
    env = LocalEnvironment(workdir=".")

    workflow = CIFixWorkflow(max_attempts=args.max_attempts)
    success = workflow.run(log, agent, env)

    if success:
        print("CI fix: SUCCESS")
        return 0
    else:
        print(f"CI fix: FAILED after {len(workflow.attempts)} attempts")
        return 1


def run_research(args: argparse.Namespace) -> int:
    """Execute the research command."""
    from chimera.core.agent import Agent
    from chimera.env.local import LocalEnvironment
    from chimera.providers.factory import create_provider
    from chimera.research.researcher import Researcher

    provider = create_provider(model=args.model)
    agent = Agent(provider=provider)
    env = LocalEnvironment(workdir=args.workdir)

    researcher = Researcher()
    result = researcher.run(args.question, agent, env)
    print(result)
    return 0


def run_docs(args: argparse.Namespace) -> int:
    """Execute the docs command."""
    from chimera.docs.generator import DocGenerator

    generator = DocGenerator(root=args.source, output_dir=args.output)
    sections = generator.scan()
    written = generator.write(sections)

    print(f"Generated {len(written)} documentation files:")
    for path in written:
        print(f"  {path}")
    return 0


def run_testgen(args: argparse.Namespace) -> int:
    """Execute the testgen command."""
    from pathlib import Path

    from chimera.testgen.generator import TestGenerator

    source_dir = Path(args.source)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    generator = TestGenerator()
    total_cases = 0

    for filepath in sorted(source_dir.rglob("*.py")):
        if any(part.startswith(".") or part in ("__pycache__", "venv")
               for part in filepath.parts):
            continue
        cases = generator.analyze(str(filepath))
        if cases:
            total_cases += len(cases)
            test_filename = f"test_{filepath.stem}.py"
            out_path = output_dir / test_filename
            lines = [f"# Auto-generated tests for {filepath}\n"]
            for case in cases:
                lines.append(case.test_code)
                lines.append("")
            out_path.write_text("\n".join(lines))

    print(f"Generated {total_cases} test cases from {args.source}")
    return 0


def run_migrate(args: argparse.Namespace) -> int:
    """Execute the migrate command."""
    from pathlib import Path

    from chimera.migration.planner import MigrationPlanner

    try:
        planner = MigrationPlanner.from_preset(args.preset)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    source_dir = Path(args.source)
    files: dict[str, str] = {}
    for filepath in sorted(source_dir.rglob("*")):
        if filepath.is_file():
            try:
                files[str(filepath.relative_to(source_dir))] = filepath.read_text()
            except Exception:
                continue

    result = planner.apply(files)

    changed = 0
    for rel_path, content in result.items():
        if content != files.get(rel_path, ""):
            out_path = source_dir / rel_path
            out_path.write_text(content)
            changed += 1
            print(f"  migrated: {rel_path}")

    print(f"Migration '{args.preset}': {changed} files changed")
    return 0


def run_plugins(args: argparse.Namespace) -> int:
    """Execute the plugins command.

    Plugins are Python packages registered via the ``chimera.plugins`` entry
    point group — installation goes through pip/uv like any other dep.
    This command lists/searches what's currently installed; it does not run
    a remote marketplace.
    """
    from chimera.plugins.manager import PluginManager

    manager = PluginManager()
    try:
        discovered = manager.discover()
    except Exception as exc:
        print(f"Error discovering plugins: {exc}", file=sys.stderr)
        return 1

    if args.action == "search":
        query = (args.query or "").lower().strip()
        if not query:
            # Empty query = list everything
            matches = discovered
        else:
            matches = [name for name in discovered if query in name.lower()]
        if not matches:
            if discovered:
                print(f"No plugins match '{args.query}'. Installed: {', '.join(discovered)}")
            else:
                print(
                    "No plugins installed.\n"
                    "Chimera plugins ship as Python packages registered via the\n"
                    "`chimera.plugins` entry point group. Install with:\n"
                    "  pip install chimera-plugin-<name>\n"
                    "  # or\n"
                    "  uv pip install chimera-plugin-<name>"
                )
            return 0
        for name in matches:
            print(f"  {name}")
        return 0

    if args.action == "install":
        if not args.query:
            print("Error: install requires a plugin name", file=sys.stderr)
            return 1
        print(
            f"Chimera doesn't run its own installer — plugins are Python packages.\n"
            f"To install '{args.query}':\n"
            f"  pip install {args.query}\n"
            f"  # or\n"
            f"  uv pip install {args.query}\n"
            f"Then verify with: chimera plugins search {args.query}",
            file=sys.stderr,
        )
        return 1

    if args.action == "uninstall":
        if not args.query:
            print("Error: uninstall requires a plugin name", file=sys.stderr)
            return 1
        if args.query not in discovered:
            print(f"Plugin '{args.query}' is not installed.", file=sys.stderr)
            return 1
        print(
            f"Chimera doesn't run its own uninstaller — use pip/uv:\n"
            f"  pip uninstall {args.query}\n"
            f"  # or\n"
            f"  uv pip uninstall {args.query}",
            file=sys.stderr,
        )
        return 1

    return 1


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
    elif args.command == "code":
        from chimera.cli.code import run_code
        return run_code(args)
    elif args.command == "review":
        return run_review(args)
    elif args.command == "ci-fix":
        return run_ci_fix(args)
    elif args.command == "research":
        return run_research(args)
    elif args.command == "docs":
        return run_docs(args)
    elif args.command == "testgen":
        return run_testgen(args)
    elif args.command == "migrate":
        return run_migrate(args)
    elif args.command == "plugins":
        return run_plugins(args)
    elif args.command == "fs":
        return args.func(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
