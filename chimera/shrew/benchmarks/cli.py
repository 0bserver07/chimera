"""``chimera shrew bench`` CLI dispatch.

Late-bound by :func:`chimera.shrew.cli._dispatch_bench` so the shrew
scaffold's ``--help`` / ``--version`` paths don't import the eval
harness or the provider factory.

Two benchmarks are wired:

* ``aider-polyglot`` — :class:`~chimera.shrew.benchmarks.aider_polyglot.
  AiderPolyglot`. Per-language code-edit tasks scored by diff-match or
  test-pass.
* ``gaia`` — :class:`~chimera.shrew.benchmarks.gaia.GAIA`. Research-task
  Q&A scored by GAIA-style answer-match.

Both adapters skip cleanly with a friendly setup hint when the staged
dataset is absent (``CHIMERA_AIDER_POLYGLOT_PATH`` /
``CHIMERA_GAIA_PATH`` overrides honored). The setup hint is printed to
stderr and the dispatcher returns exit code ``3`` so an outer CI script
can treat "needs staging" distinctly from "ran but nothing passed".

Trademark hygiene: this module names third-party benchmarks
(``Aider Polyglot``, ``GAIA``) but never names the upstream small-model
coding agent in source / docs / help text.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.eval.harness import EvalResult


__all__ = [
    "VALID_BENCHES",
    "build_shrew_agent_for_eval",
    "run_aider_polyglot",
    "run_gaia",
    "dispatch_bench",
]


VALID_BENCHES: tuple[str, ...] = ("aider-polyglot", "gaia")
"""Benchmark names accepted by :func:`dispatch_bench`."""


# ---------------------------------------------------------------------------
# Agent construction (mirrors otter.benchmarks.build_otter_agent_for_eval)
# ---------------------------------------------------------------------------


def build_shrew_agent_for_eval(model: str | None = None) -> "Agent":
    """Construct an :class:`~chimera.core.agent.Agent` matching shrew defaults.

    Mirrors :func:`chimera.shrew.cli._run_print_mode` agent assembly:
    provider via :func:`chimera.providers.factory.create_provider`, the
    full :data:`chimera.core.tool_group.AGENT_TOOLS` set, and a default
    :class:`~chimera.core.loop.ReAct` loop. The ``--allowed-tools``
    filter from the CLI surface is **not** re-applied here — benchmark
    runs deliberately use the broadest tool surface so the model can
    succeed; the small-model defaults bite at production-call time.

    Args:
        model: Model identifier resolved by
            :func:`chimera.providers.factory.create_provider`. ``None``
            falls back to ``$SHREW_MODEL`` and finally to
            :data:`chimera.shrew.cli._DEFAULT_MODEL`.

    Returns:
        A live :class:`Agent` ready to be passed into
        :class:`chimera.eval.harness.Harness`.
    """
    # Lazy imports keep this module importable without dragging in
    # provider SDKs. Tests can mock ``build_shrew_agent_for_eval``
    # directly to avoid touching any of these paths.
    from chimera.core.agent import Agent
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.prompt import Prompt
    from chimera.core.tool_group import AGENT_TOOLS
    from chimera.providers.factory import create_provider
    from chimera.shrew.cli import _DEFAULT_MODEL

    resolved = model or os.environ.get("SHREW_MODEL") or _DEFAULT_MODEL
    provider = create_provider(model=resolved)
    loop = ReAct(config=LoopConfig())
    prompt = Prompt.from_string(
        "You are Shrew, a small-model Chimera coding agent under "
        "benchmark evaluation. Read the task carefully, use tools "
        "frugally, and produce the requested output."
    )
    return Agent(
        provider=provider,
        tools=list(AGENT_TOOLS),
        loop=loop,
        prompt=prompt,
        name="shrew-bench",
    )


# ---------------------------------------------------------------------------
# Benchmark runners
# ---------------------------------------------------------------------------


def run_aider_polyglot(
    limit: int,
    model: str,
    dataset_path: str | None = None,
    language: str | None = None,
    agent_factory: Callable[[str], "Agent"] | None = None,
) -> "EvalResult":
    """Run the Aider Polyglot benchmark against a shrew Agent.

    Args:
        limit: Maximum number of tasks (``0`` / negative → unlimited).
        model: Model identifier passed to the agent factory.
        dataset_path: Optional override for the dataset root.
        language: Optional language filter (``"python"`` / ``"rust"`` /
            etc.). When set, only matching tasks are evaluated.
        agent_factory: Optional callable returning an :class:`Agent`.
            Used by tests to inject a mock without booting a provider.
            When ``None``, :func:`build_shrew_agent_for_eval` is used.

    Returns:
        The :class:`~chimera.eval.harness.EvalResult` for the run.

    Raises:
        FileNotFoundError: When the dataset is not staged. The caller
            (:func:`dispatch_bench`) catches this and prints the setup
            hint.
    """
    from pathlib import Path

    from chimera.eval.harness import Harness
    from chimera.shrew.benchmarks.aider_polyglot import (
        AiderPolyglot,
        dataset_available,
        default_dataset_path,
        setup_hint,
    )

    resolved = (
        Path(dataset_path).expanduser() if dataset_path else default_dataset_path()
    )
    if not dataset_available(resolved):
        raise FileNotFoundError(setup_hint(resolved))

    effective_limit = limit if limit and limit > 0 else None
    benchmark = AiderPolyglot(
        dataset_path=str(resolved),
        limit=effective_limit,
        language=language,
    )
    factory = agent_factory or build_shrew_agent_for_eval
    agent = factory(model)
    harness = Harness(benchmark=benchmark, agent=agent)
    return harness.run()


def run_gaia(
    limit: int,
    model: str,
    dataset_path: str | None = None,
    level: int | None = None,
    agent_factory: Callable[[str], "Agent"] | None = None,
) -> "EvalResult":
    """Run the GAIA benchmark against a shrew Agent.

    Args:
        limit: Maximum number of tasks (``0`` / negative → unlimited).
        model: Model identifier passed to the agent factory.
        dataset_path: Optional override for the dataset root.
        level: Optional difficulty filter (``1`` / ``2`` / ``3``).
        agent_factory: Optional callable returning an :class:`Agent`.

    Returns:
        The :class:`~chimera.eval.harness.EvalResult` for the run.

    Raises:
        FileNotFoundError: When the dataset is not staged.
    """
    from pathlib import Path

    from chimera.eval.harness import Harness
    from chimera.shrew.benchmarks.gaia import (
        GAIA,
        dataset_available,
        default_dataset_path,
        setup_hint,
    )

    resolved = (
        Path(dataset_path).expanduser() if dataset_path else default_dataset_path()
    )
    if not dataset_available(resolved):
        raise FileNotFoundError(setup_hint(resolved))

    effective_limit = limit if limit and limit > 0 else None
    benchmark = GAIA(
        dataset_path=str(resolved),
        limit=effective_limit,
        level=level,
    )
    factory = agent_factory or build_shrew_agent_for_eval
    agent = factory(model)
    harness = Harness(benchmark=benchmark, agent=agent)
    return harness.run()


# ---------------------------------------------------------------------------
# Pretty-printing
# ---------------------------------------------------------------------------


def _print_eval_result(result: "EvalResult") -> None:
    """Print a one-line summary of an :class:`EvalResult` to stdout."""
    print(
        f"{result.benchmark}: passed={result.passed}/{result.total} "
        f"rate={result.pass_rate:.1%} cost=${result.total_cost:.4f}"
    )


# ---------------------------------------------------------------------------
# CLI dispatch (late-bound from chimera.shrew.cli)
# ---------------------------------------------------------------------------


def dispatch_bench(args: argparse.Namespace) -> int:
    """Implement ``chimera shrew bench [aider-polyglot|gaia]``.

    Args slots used:
        * ``sub_action`` — benchmark name. ``terminal-bench`` is
          accepted by the parser for forward-compat but currently
          surfaces a ``not yet wired`` message + exit code 3.
        * ``model`` — model identifier (already populated by
          :func:`chimera.shrew.cli.add_arguments`).
        * ``bench_limit`` — optional integer limit; default 5.

    Returns:
        Process exit code:

        * ``0`` — benchmark ran and at least one task passed.
        * ``1`` — benchmark ran, nothing passed.
        * ``2`` — malformed invocation (missing / unknown benchmark).
        * ``3`` — dataset not staged, or runtime failure during the run.
    """
    bench_name = (getattr(args, "sub_action", None) or "").strip().lower()
    if not bench_name:
        print(
            "error: 'shrew bench' requires a benchmark name. "
            f"Choose one of: {', '.join(VALID_BENCHES)}.",
            file=sys.stderr,
        )
        return 2

    if bench_name == "terminal-bench":
        # Reserved by the parser for parity with otter / mink. Not yet
        # wired in this scaffold — the polyglot + GAIA pair is the
        # smallest useful surface to ship for shrew's small-model focus.
        print(
            "shrew bench terminal-bench: not yet wired in this scaffold "
            "(see research/shrew/SPEC.md, agent S4).",
            file=sys.stderr,
        )
        return 3

    if bench_name not in VALID_BENCHES:
        print(
            f"error: unknown benchmark {bench_name!r}. "
            f"Choose one of: {', '.join(VALID_BENCHES)}.",
            file=sys.stderr,
        )
        return 2

    model = getattr(args, "model", None) or ""
    limit = int(getattr(args, "bench_limit", 0) or 0)
    if limit <= 0:
        # Default of 5 keeps an unguarded ``shrew bench gaia`` from
        # kicking off all 165 validation tasks (paid LLM calls!) when
        # the user just wants to smoke-test the wiring.
        limit = 5

    runner: Callable[..., Any]
    if bench_name == "aider-polyglot":
        runner = run_aider_polyglot
    else:  # gaia
        runner = run_gaia

    try:
        result = runner(limit=limit, model=model)
    except FileNotFoundError as exc:
        # Friendly skip with the setup hint as the message body.
        print(f"shrew bench {bench_name}: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 — surface runtime failures
        print(
            f"shrew bench {bench_name}: failed to run "
            f"({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
        return 3

    _print_eval_result(result)
    return 0 if result.passed > 0 else 1
