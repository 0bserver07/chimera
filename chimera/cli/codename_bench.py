"""Shared ``chimera <frontend> bench`` delegation to the canonical harness.

The codename CLIs (``ferret`` / ``stoat`` / ``badger``) are frontends over the
same assembly stack; their ``bench`` subcommand must not reimplement
evaluation. It delegates to the one canonical ``bench-matrix`` runner so every
frontend measures with identical graders, budgets, and registry — one harness,
every frontend.

- ``chimera <frontend> bench`` (or ``bench list``) prints the registered
  benchmark names.
- ``chimera <frontend> bench <suite>`` runs that benchmark through
  ``run_bench_matrix`` with a default single agent under a shared budget,
  honoring any ``model`` / ``limit`` / budget fields the frontend already
  parsed.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Callable


def dispatch_codename_bench(
    args: argparse.Namespace,
    frontend: str,
    runner: Callable[[argparse.Namespace], int] | None = None,
) -> int:
    """Delegate a codename ``bench`` subcommand to the canonical matrix runner.

    Args:
        args: The frontend's parsed namespace. The suite name is read from
            ``sub_action``; ``model`` / ``limit`` / budget fields are honored
            when present, otherwise sensible defaults apply.
        frontend: The codename (e.g. ``"ferret"``) for user-facing messages.
        runner: Injection seam for tests — defaults to the real
            ``run_bench_matrix``.

    Returns:
        Process exit code: ``0`` for the list path, the runner's code for a
        real run, ``2`` for an unknown suite.
    """
    from chimera.cli.main import _BENCHMARKS

    suite = getattr(args, "sub_action", None)

    if not suite or suite == "list":
        print(
            f"{frontend} bench — registered suites (via the canonical harness):",
            file=sys.stderr,
        )
        for name in sorted(_BENCHMARKS):
            print(f"  {name}", file=sys.stderr)
        print(f"\nRun one:  chimera {frontend} bench <suite>", file=sys.stderr)
        return 0

    if suite not in _BENCHMARKS:
        print(
            f"{frontend} bench: unknown suite {suite!r}. "
            f"Try `chimera {frontend} bench list`.",
            file=sys.stderr,
        )
        return 2

    if runner is None:
        from chimera.cli.bench_matrix import run_bench_matrix

        runner = run_bench_matrix

    ns = argparse.Namespace(
        agents=_opt(args, "agents") or "react",
        benchmarks=suite,
        model=_opt(args, "model") or os.environ.get("ANTHROPIC_MODEL", "glm-5"),
        limit=_opt(args, "limit") or 1,
        dataset=_opt(args, "dataset"),
        registry=_opt(args, "registry"),
        max_tool_calls=_opt(args, "max_tool_calls") or 15,
        max_llm_calls=_opt(args, "max_llm_calls") or 15,
        max_wall_clock=_opt(args, "max_wall_clock"),
        max_cost=_opt(args, "max_cost") or 0.15,
        fmt=_opt(args, "fmt") or "terminal",
        output=_opt(args, "output"),
        env_kind=_opt(args, "env_kind") or "local",
    )
    print(
        f"[{frontend}] delegating to canonical bench-matrix: "
        f"{suite} x {ns.agents} (model={ns.model})",
        file=sys.stderr,
    )
    return runner(ns)


def _opt(args: argparse.Namespace, name: str) -> Any:
    """Return ``args.<name>`` if present and truthy-or-zero, else ``None``."""
    return getattr(args, name, None)
