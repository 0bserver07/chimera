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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from chimera.env.base import Environment

# Managed-sandbox --env values -> (factory provider key, required env var).
# Kept as data so adding a backend is one row here plus one in
# chimera.env.factory, and so the credential gate stays testable in isolation.
_SANDBOX_ENVS: dict[str, tuple[str, str]] = {
    "e2b": ("e2b", "E2B_API_KEY"),
    "daytona": ("daytona", "DAYTONA_API_KEY"),
}


def missing_sandbox_credentials(env_kind: str) -> str | None:
    """Return the env var a managed-sandbox backend needs but does not have.

    Args:
        env_kind: One of the keys of :data:`_SANDBOX_ENVS`.

    Returns:
        The name of the missing environment variable, or ``None`` when
        credentials are present.

    Raises:
        KeyError: If *env_kind* is not a managed-sandbox backend.
    """
    import os

    _, env_var = _SANDBOX_ENVS[env_kind]
    return None if os.environ.get(env_var) else env_var


def _sandbox_env_factory(
    env_kind: str, image: str | None
) -> Callable[[], Environment]:
    """Build a zero-arg factory that provisions one fresh sandbox per task.

    The harness calls ``env.setup()`` itself for every task, so this must only
    *construct* the environment — provisioning here would leak a sandbox.

    Args:
        env_kind: One of the keys of :data:`_SANDBOX_ENVS`.
        image: Image/template override, or ``None`` for the service default.

    Returns:
        A callable returning a fresh, un-``setup`` :class:`Environment`.
    """
    from chimera.env.factory import create_environment

    provider, _ = _SANDBOX_ENVS[env_kind]
    # E2B calls it a template, Daytona calls it an image.
    key = "template" if provider == "e2b" else "image"
    opts: dict[str, Any] = {key: image} if image else {}

    def _make() -> Environment:
        return create_environment(provider, **opts)

    return _make


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
        choices=("local", "none", "modal", "swe-modal", "e2b", "daytona"),
        default="local",
        help="Per-task environment: 'local' fresh temp-dir (default), 'none', "
        "'modal' — every task in a fresh Modal cloud sandbox — 'swe-modal' — "
        "run each SWE-bench instance in ITS per-instance evaluation image on "
        "Modal — or 'e2b' / 'daytona' for a fresh managed sandbox per task on "
        "those services. Every cloud backend fails loudly without credentials "
        "rather than silently running locally.",
    )
    p.add_argument(
        "--modal-gpu",
        default=None,
        help="Modal GPU for --env modal/swe-modal (e.g. H100, A100, T4, 'A100:2'). "
        "Omit for a CPU-only sandbox.",
    )
    p.add_argument(
        "--modal-image",
        default=None,
        help="Container image override for --env modal/swe-modal. For 'modal' an "
        "unset value defaults to python:3.11-slim; for 'swe-modal' an unset value "
        "means each task uses its own per-instance SWE image — set this to force a "
        "fixed image (e.g. a small image for a plumbing smoke test).",
    )
    p.add_argument(
        "--sandbox-image",
        default=None,
        help="Image/template for --env e2b|daytona: an E2B template name "
        "(unset defaults to 'base') or a Daytona Docker image (unset uses the "
        "account's default snapshot). Ignored by the other --env values.",
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
    elif args.env_kind in ("modal", "swe-modal"):
        # Both run tasks on Modal cloud sandboxes and need Modal auth — env vars
        # OR the CLI config written by `modal setup` / `modal token new`
        # (~/.modal.toml, the common case). Without it the sandbox silently
        # falls back to in-memory and cells would not reflect real cloud
        # execution, so fail loudly here instead.
        import os
        from pathlib import Path

        modal_authed = bool(
            os.environ.get("MODAL_TOKEN_ID")
            or os.environ.get("MODAL_TOKEN_SECRET")
            or (Path.home() / ".modal.toml").exists()
        )
        if not modal_authed:
            print(
                f"chimera bench-matrix --env {args.env_kind}: no Modal auth "
                "found. Run `modal setup` (writes ~/.modal.toml) or set "
                "MODAL_TOKEN_ID / MODAL_TOKEN_SECRET.",
                file=sys.stderr,
            )
            return 2

        gpu = args.modal_gpu

        if args.env_kind == "modal":
            from chimera.env.modal_sandbox import ModalSandboxEnvironment

            image = args.modal_image or "python:3.11-slim"

            def _modal_env() -> ModalSandboxEnvironment:
                # Harness calls env.setup() per task; do NOT set up here or a
                # duplicate sandbox leaks.
                return ModalSandboxEnvironment(image=image, gpu=gpu)

            env_factory = _modal_env
            _where = f"GPU={gpu}" if gpu else "CPU-only"
            print(
                f"Per-task environment: Modal sandbox ({image}, {_where})",
                file=sys.stderr,
            )
        else:  # swe-modal — each SWE instance in ITS per-instance image
            from chimera.eval.benchmarks.swe_bench import SweModalEnvFactory

            # run_matrix shares ONE env_factory across all benchmark columns,
            # and a SweModalEnvFactory is bound to one benchmark's task list, so
            # swe-modal takes a single (SWE-family) benchmark per invocation.
            if len(benchmarks) != 1:
                print(
                    "chimera bench-matrix --env swe-modal expects exactly one "
                    f"benchmark (got {len(benchmarks)}); the per-instance image "
                    "factory is bound to one benchmark's task list.",
                    file=sys.stderr,
                )
                return 2

            # Unset --modal-image => per-instance images; set => fixed override.
            env_factory = SweModalEnvFactory(
                benchmarks[0].tasks(), gpu=gpu, image=args.modal_image
            )
            _where = f"GPU={gpu}" if gpu else "CPU-only"
            _imgnote = (
                f"fixed image {args.modal_image}"
                if args.modal_image
                else "per-instance images"
            )
            print(
                f"Per-task environment: SWE-bench on Modal ({_imgnote}, {_where})",
                file=sys.stderr,
            )
    elif args.env_kind in _SANDBOX_ENVS:
        # Managed sandbox services (E2B, Daytona). Same posture as Modal: a
        # missing key must stop the run, because a matrix cell produced
        # locally is indistinguishable from one produced in the cloud.
        missing = missing_sandbox_credentials(args.env_kind)
        if missing is not None:
            print(
                f"chimera bench-matrix --env {args.env_kind}: no credentials "
                f"found. Set {missing} (see "
                "docs/guides/remote-and-cloud-environments.md).",
                file=sys.stderr,
            )
            return 2

        env_factory = _sandbox_env_factory(args.env_kind, args.sandbox_image)
        _imgnote = args.sandbox_image or "service default image"
        print(
            f"Per-task environment: {args.env_kind} sandbox ({_imgnote})",
            file=sys.stderr,
        )

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
