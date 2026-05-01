"""``chimera otter bench`` — wire benchmark adapters to an otter Agent.

This module connects the existing :mod:`chimera.eval` harness + benchmark
adapters to an Agent constructed with otter's defaults. Two benchmarks
are wired here:

* **HumanEval** — code-generation pass@1 against the standard 164-task
  dataset (vendored under ``data/humaneval.json`` for offline runs).
* **tau-bench** — multi-turn tool-use evaluation; falls back to a clear
  :class:`NotImplementedError` when the upstream dataset is not staged
  under ``~/.chimera/datasets/tau-bench/``.

The Agent built here uses the same provider factory + tool group as
:func:`chimera.otter.cli._run_print_mode`, so a benchmark run reflects
the production ``chimera otter -p ...`` configuration as closely as
possible without dragging in TUI/streaming machinery.

CLI hook: :func:`dispatch_bench` is the late-bound handler invoked by
:func:`chimera.otter.cli.run` when the user passes
``chimera otter bench [humaneval|tau-bench] [--limit N] [--model M]``.

Trademark hygiene: this module never names the upstream open-source
coding agent in source/docs/help text; tau-bench is a third-party
benchmark and naming it explicitly is fine (it isn't the upstream brand).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.eval.harness import EvalResult


__all__ = [
    "build_otter_agent_for_eval",
    "run_humaneval",
    "run_mbpp",
    "run_tau_bench",
    "dispatch_bench",
    "DEFAULT_HUMANEVAL_PATH",
]


# Repo-relative path to the vendored HumanEval dump. We resolve this
# lazily inside :func:`run_humaneval` so callers that override
# ``dataset_path`` never touch the filesystem here.
DEFAULT_HUMANEVAL_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "humaneval.json"
)
"""Default vendored HumanEval JSON path (``<repo>/data/humaneval.json``)."""


# ---------------------------------------------------------------------------
# Agent construction
# ---------------------------------------------------------------------------


def _strip_code_fences(text: str) -> str:
    """Extract a Python code block from a markdown-fenced agent reply.

    Coding agents like otter routinely emit ```python ... ``` blocks
    around the implementation. The HumanEval evaluator passes the raw
    agent output to ``exec()`` so unparsed fences become a syntax error.

    Strategy:

    * If at least one ``````` fence is present, return the
      concatenation of every fenced block's body in source order. This
      handles agents that emit imports + helper + main function across
      multiple blocks.
    * Otherwise return *text* unchanged so callers that already produce
      raw Python see no behavioral change.
    """
    if "```" not in text:
        return text
    parts: list[str] = []
    inside = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```"):
            inside = not inside
            continue
        if inside:
            parts.append(line)
    if not parts:
        return text
    return "\n".join(parts)


def build_otter_agent_for_eval(model: str | None = None) -> "Agent":
    """Construct an :class:`~chimera.core.agent.Agent` matching otter defaults.

    Mirrors :func:`chimera.otter.cli._run_print_mode` agent assembly:
    provider via :func:`chimera.providers.factory.create_provider`, the
    full :data:`chimera.core.tool_group.AGENT_TOOLS` set, the otter
    base prompt, and a default :class:`~chimera.core.loop.ReAct` loop.

    Tools, prompt, and loop are constructed inline (no streaming
    handler, no permission policy, no message queues) so the harness
    can iterate quickly across N tasks without TUI plumbing.

    Args:
        model: Model identifier resolved by
            :func:`chimera.providers.factory.create_provider`. ``None``
            falls back to ``$OTTER_MODEL`` and finally to
            :data:`chimera.otter.cli._DEFAULT_MODEL`.

    Returns:
        A live :class:`Agent` ready to be passed into
        :class:`chimera.eval.harness.Harness`.
    """
    # WHY: lazy imports keep this module importable without dragging in
    # the provider SDKs or the agent loop. Tests that mock
    # ``build_otter_agent_for_eval`` itself never trigger any of these.
    from chimera.core.agent import Agent
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.prompt import Prompt
    from chimera.core.tool_group import AGENT_TOOLS
    from chimera.otter.cli import _DEFAULT_MODEL, _build_provider

    resolved = model or os.environ.get("OTTER_MODEL") or _DEFAULT_MODEL
    provider = _build_provider(resolved)
    loop = ReAct(config=LoopConfig())
    prompt = Prompt.from_string(
        "You are Otter, a Chimera coding agent under benchmark evaluation. "
        "Read the task carefully, then produce the requested output. "
        "When asked for a Python function: emit ONLY the complete function "
        "definition (and any required imports) inside a single "
        "```python ... ``` fenced code block. The block must execute "
        "standalone with `exec()` and define the requested function. Do "
        "NOT call the function, do NOT print anything, do NOT include "
        "test code, do NOT wrap in `if __name__ == '__main__':`. No prose "
        "outside the code block."
    )
    return Agent(
        provider=provider,
        tools=list(AGENT_TOOLS),
        loop=loop,
        prompt=prompt,
        name="otter-bench",
    )


# ---------------------------------------------------------------------------
# HumanEval runner
# ---------------------------------------------------------------------------


def run_humaneval(
    limit: int,
    model: str,
    dataset_path: str | None = None,
) -> "EvalResult":
    """Run the HumanEval benchmark against an otter Agent.

    Wires :class:`chimera.eval.benchmarks.human_eval.HumanEval` into a
    :class:`chimera.eval.harness.Harness` driven by an Agent built via
    :func:`build_otter_agent_for_eval`. Tasks are loaded from the
    vendored ``data/humaneval.json`` by default.

    A per-task :class:`~chimera.env.local.LocalEnvironment` is provided
    via ``env_factory`` so file/list/bash tools have a working sandbox.
    Each task gets a fresh temporary working directory which the
    harness sets up + cleans up around the agent run.

    Args:
        limit: Maximum number of tasks to run. Use ``0`` or a negative
            value to run the full benchmark.
        model: Model identifier passed to
            :func:`build_otter_agent_for_eval`.
        dataset_path: Optional override for the HumanEval JSON dump.
            When ``None``, the vendored
            :data:`DEFAULT_HUMANEVAL_PATH` is used.

    Returns:
        The :class:`EvalResult` aggregated across the requested tasks.
    """
    import tempfile

    from chimera.env.local import LocalEnvironment
    from chimera.eval.benchmarks.human_eval import HumanEval
    from chimera.eval.harness import Harness

    path = dataset_path or str(DEFAULT_HUMANEVAL_PATH)
    effective_limit = limit if limit and limit > 0 else None

    # WHY: forward construction through ``HumanEval`` itself (rather than
    # always instantiating a subclass) so tests that ``patch("chimera.eval
    # .benchmarks.human_eval.HumanEval")`` observe the call. When the real
    # class is in play we then attach the otter-side fence-strip +
    # check-invocation fixes by monkey-patching the ``evaluate`` method on
    # the instance — equivalent to subclassing without breaking the mock.
    benchmark = HumanEval(dataset_path=path, limit=effective_limit)

    if isinstance(HumanEval, type):  # not a Mock; safe to patch evaluate
        _orig_evaluate = benchmark.evaluate

        def _patched_evaluate(task: Any, agent_output: str, env: Any) -> bool:
            """Strip markdown fences and append ``check(<entry_point>)``.

            The base ``evaluate()`` ``exec()``s ``solution + test_code``
            but never calls ``check``, so any syntactically-valid output
            passes. We inject the call.
            """
            cleaned = _strip_code_fences(agent_output)
            entry_point = task.get("entry_point", "")
            test_code = task.get("test", "")
            if not test_code:
                return bool(_orig_evaluate(task, cleaned, env))
            invocation = (
                f"\n\ncheck({entry_point})\n" if entry_point else "\n"
            )
            patched_test = test_code + invocation
            patched_task = dict(task)
            patched_task["test"] = patched_test
            return bool(_orig_evaluate(patched_task, cleaned, env))

        benchmark.evaluate = _patched_evaluate  # type: ignore[method-assign]
    agent = build_otter_agent_for_eval(model)

    def _env_factory() -> LocalEnvironment:
        # WHY: AGENT_TOOLS includes list_files / bash / read / write which
        # require a non-None env. Give each task a fresh temp workdir so
        # tool calls land somewhere safe and isolated from the repo root.
        workdir = tempfile.mkdtemp(prefix="otter-humaneval-")
        return LocalEnvironment(workdir=workdir)

    harness = Harness(benchmark=benchmark, agent=agent, env_factory=_env_factory)
    return harness.run()


# ---------------------------------------------------------------------------
# MBPP runner
# ---------------------------------------------------------------------------


def run_mbpp(
    limit: int,
    model: str,
    dataset_path: str | None = None,
    split: str = "sanitized",
) -> "EvalResult":
    """Run the MBPP benchmark against an otter Agent.

    Mirrors :func:`run_humaneval` but targets
    :class:`chimera.eval.benchmarks.mbpp.MBPP`. The MBPP dataset is
    **not vendored** (footprint + license attribution); when no dataset
    is staged, this function raises a clear :class:`NotImplementedError`
    pointing at the staging steps. See
    :data:`chimera.eval.benchmarks.mbpp.SETUP_HINT` and
    :func:`chimera.eval.benchmarks.mbpp.default_dataset_path`.

    The agent's output is markdown-fence-stripped (MBPP records lack a
    ``check(<entry_point>)`` invocation — assertions are inlined — so we
    only patch the fence stripper, not the test invocation).

    Args:
        limit: Maximum number of tasks to run. ``0`` / negative means
            unlimited (run the full split).
        model: Model identifier passed to
            :func:`build_otter_agent_for_eval`.
        dataset_path: Optional override for the MBPP JSON/JSONL dump.
            When ``None``, :func:`default_dataset_path` is used.
        split: Logical split name (``"sanitized"`` / ``"test"`` /
            ``"validation"`` / ``"train"`` / ``"prompt"``). Surfaced via
            ``MBPP.name()`` only — does not filter records.

    Returns:
        The :class:`EvalResult` aggregated across the requested tasks.

    Raises:
        NotImplementedError: When no MBPP dataset is staged. The error
            message contains the staging steps and the
            ``CHIMERA_MBPP_PATH`` override.
    """
    import tempfile

    from chimera.env.local import LocalEnvironment
    from chimera.eval.benchmarks.mbpp import (
        MBPP,
        SETUP_HINT,
        dataset_available,
        default_dataset_path,
    )
    from chimera.eval.harness import Harness

    resolved = (
        Path(dataset_path).expanduser() if dataset_path else default_dataset_path()
    )
    if not dataset_available(resolved):
        raise NotImplementedError(
            "MBPP dataset not staged for otter eval.\n"
            f"  expected: {resolved}\n"
            f"  override: CHIMERA_MBPP_PATH=/abs/path/to/file.json[l]\n\n"
            f"{SETUP_HINT}"
        )

    effective_limit = limit if limit and limit > 0 else None
    benchmark = MBPP(
        dataset_path=str(resolved), split=split, limit=effective_limit
    )

    if isinstance(MBPP, type):  # not a Mock; safe to patch evaluate
        _orig_evaluate = benchmark.evaluate

        def _patched_evaluate(task: Any, agent_output: str, env: Any) -> bool:
            """Strip markdown fences before handing output to MBPP.evaluate."""
            cleaned = _strip_code_fences(agent_output)
            return bool(_orig_evaluate(task, cleaned, env))

        benchmark.evaluate = _patched_evaluate  # type: ignore[method-assign]

    agent = build_otter_agent_for_eval(model)

    def _env_factory() -> LocalEnvironment:
        # WHY: AGENT_TOOLS expects a non-None env. Each MBPP task gets a
        # fresh temp workdir so list_files / bash / write land safely.
        workdir = tempfile.mkdtemp(prefix="otter-mbpp-")
        return LocalEnvironment(workdir=workdir)

    harness = Harness(benchmark=benchmark, agent=agent, env_factory=_env_factory)
    return harness.run()


# ---------------------------------------------------------------------------
# tau-bench runner
# ---------------------------------------------------------------------------


def run_tau_bench(
    limit: int,
    model: str,
    domain: str = "airline",
    dataset_path: str | None = None,
) -> "EvalResult":
    """Run the tau-bench benchmark against an otter Agent.

    Wires :class:`chimera.eval.benchmarks.tau_bench.TauBench` into a
    :class:`chimera.eval.harness.Harness`. tau-bench requires a local
    dataset (we do **not** vendor or pip-install upstream); when no
    dataset is staged, this function raises a clear
    :class:`NotImplementedError` so callers see the setup hint
    immediately.

    The setup hint mirrors :data:`chimera.eval.benchmarks.tau_bench.
    _SETUP_HINT` minus the trailing newline so callers can wrap it.

    Args:
        limit: Maximum number of tasks to run. ``0`` / negative means
            unlimited.
        model: Model identifier passed to
            :func:`build_otter_agent_for_eval`.
        domain: One of ``"airline"``, ``"retail"``, ``"telecom"``,
            ``"banking"``, ``"mock"``. Default ``"airline"``.
        dataset_path: Optional override for the dataset directory or
            file. When ``None``, the resolved
            :func:`chimera.eval.benchmarks.tau_bench.default_dataset_path`
            is used.

    Returns:
        The :class:`EvalResult` aggregated across the requested tasks.

    Raises:
        NotImplementedError: When no tau-bench dataset is staged. The
            error message points at the staging steps and the
            ``CHIMERA_TAU_BENCH_PATH`` override.
    """
    from chimera.eval.benchmarks.tau_bench import (
        TauBench,
        dataset_available,
        default_dataset_path,
    )
    from chimera.eval.harness import Harness

    resolved_path = (
        Path(dataset_path).expanduser() if dataset_path else default_dataset_path()
    )
    if not dataset_available(resolved_path, domain=domain):
        raise NotImplementedError(
            "tau-bench dataset not staged for otter eval.\n"
            f"  domain:        {domain}\n"
            f"  expected dir:  {resolved_path}\n"
            "  staging steps: see chimera.eval.benchmarks.tau_bench._SETUP_HINT\n"
            "  override:      CHIMERA_TAU_BENCH_PATH=/abs/path/to/dir\n"
            "Note: M5 (mink-finishup) is wiring the live tau-bench dataset; "
            "this otter runner uses the same adapter so it will light up as "
            "soon as the dataset lands."
        )

    effective_limit = limit if limit and limit > 0 else None
    benchmark = TauBench(
        domain=domain,
        dataset_path=str(resolved_path) if dataset_path else None,
        limit=effective_limit,
    )
    agent = build_otter_agent_for_eval(model)
    harness = Harness(benchmark=benchmark, agent=agent)
    return harness.run()


# ---------------------------------------------------------------------------
# CLI hook (late-bound from chimera.otter.cli)
# ---------------------------------------------------------------------------


_VALID_BENCHES = ("humaneval", "mbpp", "tau-bench")


def _print_eval_result(result: "EvalResult") -> None:
    """Print a one-line summary of an :class:`EvalResult` to stdout."""
    print(
        f"{result.benchmark}: passed={result.passed}/{result.total} "
        f"rate={result.pass_rate:.1%} cost=${result.total_cost:.4f}"
    )


def dispatch_bench(args: argparse.Namespace) -> int:
    """Implement ``chimera otter bench [humaneval|tau-bench]``.

    This is the function :mod:`chimera.otter.cli` late-binds when the
    user passes the ``bench`` subcommand. Late binding (rather than
    direct import) keeps the otter scaffold's ``--help`` / ``--version``
    paths free of the eval harness imports.

    Args slots used:
        * ``sub_action`` — benchmark name (``humaneval`` / ``tau-bench``).
        * ``sub_target`` — when set to ``--limit=N`` / ``--model=M``,
          parsed as a kv override; otherwise unused.
        * ``model`` — model identifier (already populated by
          :func:`chimera.otter.cli.add_arguments`).
        * ``bench_limit`` — optional integer limit; defaults to ``5`` so
          a stray ``otter bench humaneval`` stays cheap.

    Args:
        args: Parsed CLI namespace.

    Returns:
        Process exit code: ``0`` when at least one task passed,
        ``1`` when the benchmark ran but nothing passed, ``2`` when the
        invocation was malformed, ``3`` on a NotImplementedError or a
        provider construction failure.
    """
    bench_name = (getattr(args, "sub_action", None) or "").strip().lower()
    if not bench_name:
        print(
            "error: 'otter bench' requires a benchmark name. "
            f"Choose one of: {', '.join(_VALID_BENCHES)}.",
            file=sys.stderr,
        )
        return 2
    if bench_name not in _VALID_BENCHES:
        print(
            f"error: unknown benchmark {bench_name!r}. "
            f"Choose one of: {', '.join(_VALID_BENCHES)}.",
            file=sys.stderr,
        )
        return 2

    model = getattr(args, "model", None) or ""
    limit = int(getattr(args, "bench_limit", 0) or 0)
    if limit <= 0:
        # WHY: a default of 5 keeps an unguarded ``otter bench humaneval``
        # from kicking off all 164 HumanEval tasks (paid LLM calls!) when
        # the user just wants to smoke-test the wiring.
        limit = 5

    try:
        if bench_name == "humaneval":
            result = run_humaneval(limit=limit, model=model)
        elif bench_name == "mbpp":
            result = run_mbpp(limit=limit, model=model)
        else:  # tau-bench
            domain = getattr(args, "bench_domain", None) or "airline"
            result = run_tau_bench(limit=limit, model=model, domain=domain)
    except NotImplementedError as exc:
        print(f"otter bench {bench_name}: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001
        print(
            f"otter bench {bench_name}: failed to run ({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
        return 3

    _print_eval_result(result)
    return 0 if result.passed > 0 else 1
