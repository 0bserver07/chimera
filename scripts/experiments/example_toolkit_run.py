#!/usr/bin/env python3
"""The ProgramBench sweep, rewritten on the experiment toolkit — and runnable.

``pb_sweep.py`` (frozen, in this directory) is 105 lines, of which roughly
forty are plumbing: parse ``.env`` by hand, pick a run directory, ``mkdir
-p``, hold a JSONL handle open and remember to flush it, collect results in a
list, write a summary at the end. None of that is the experiment. Every driver
here rewrote it, each slightly differently, and one of them wrote 336 MB into
the repo root because a run directory was chosen by hand.

This script is the same shape with the plumbing deleted::

    pb_sweep.py                                this file
    -----------------------------------------  ------------------------------
    RUN_ROOT = PB_RUNS / "2026-06-17-sweep"    run = start("example-sweep", …)
    RUN_ROOT.mkdir(parents=True, …)            (start does it, under the store)
    logf = open(RUN_ROOT / "sweep.jsonl","a")  run.jsonl("progress.jsonl", rec)
    logf.write(json.dumps(rec)); logf.flush()  (jsonl flushes)
    (no resume — a rerun redid everything)     run.seen("progress.jsonl", …)
    ws = run_dir / tid / "ws"; ws.mkdir(…)     run.subdir(f"ws/{model}/{task}")
    (RUN_ROOT/"results.json").write_text(…)    run.finish({...}) -> result.json
    (no provenance)                            manifest.json: git SHA + dirty

The workload is deliberately real but offline: two hand-written "solvers" —
one careful, one hasty — emit Python source for five small tasks, each solution
is executed in a subprocess and graded against expected output. **No model
credentials, no network, no Docker.** The point is the toolkit's shape, and a
demo you cannot run is not a demo.

Run it::

    uv run python scripts/experiments/example_toolkit_run.py
    chimera experiments list
    chimera experiments show example-sweep

Then prove the crash story end to end::

    uv run python scripts/experiments/example_toolkit_run.py --crash-after 4
    chimera experiments list                 # -> interrupted
    uv run python scripts/experiments/example_toolkit_run.py
    #   -> "resuming …, 4 unit(s) already done" and only the rest re-run

Everything lands under ``~/.chimera/experiment-runs/example-sweep/<stamp>/``
(relocate with ``$CHIMERA_HOME``; reclaim with ``chimera gc``). Nothing is
written beside this file, and nothing is written to ``data/`` — promoting a
result there stays a deliberate human act.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Any

from chimera.experiments import start

#: The experiment name — one directory under the ``experiment-runs`` store.
EXPERIMENT = "example-sweep"

#: Five tiny tasks: a call to make and the output it must print.
TASKS: list[dict[str, str]] = [
    {"id": "sum-list", "call": "solve([1, 2, 3, 4])", "expect": "10"},
    {"id": "reverse", "call": "solve('chimera')", "expect": "aremihc"},
    {"id": "count-vowels", "call": "solve('synthesis')", "expect": "2"},
    {"id": "max-gap", "call": "solve([1, 9, 3, 14])", "expect": "13"},
    {"id": "dedupe", "call": "solve([1, 1, 2, 2, 3])", "expect": "[1, 2, 3]"},
]

#: Two "solvers" standing in for two models. ``hasty`` gets three of the five
#: right, so the run produces a real mixed score rather than a uniform 0 or
#: 100% — a uniform-zero column is the harness-gap signature
#: (``docs/playbooks/13-live-bench-runs.md``), and a uniform 100% would
#: exercise none of the receipt's invariants.
SOLVERS: dict[str, dict[str, str]] = {
    "careful": {
        "sum-list": "def solve(xs):\n    return sum(xs)\n",
        "reverse": "def solve(s):\n    return s[::-1]\n",
        "count-vowels": "def solve(s):\n    return sum(c in 'aeiou' for c in s)\n",
        "max-gap": "def solve(xs):\n    return max(xs) - min(xs)\n",
        "dedupe": "def solve(xs):\n    return sorted(set(xs))\n",
    },
    "hasty": {
        "sum-list": "def solve(xs):\n    return sum(xs)\n",
        "reverse": "def solve(s):\n    return ''.join(reversed(s))\n",
        "count-vowels": "def solve(s):\n    return sum(c in 'aeiouy' for c in s)\n",  # wrong
        "max-gap": "def solve(xs):\n    return max(xs)\n",  # wrong
        "dedupe": "def solve(xs):\n    return sorted(set(xs), reverse=True)\n",  # wrong
    },
}

#: Per-solution execution budget. A hung candidate must not hang the sweep.
TIMEOUT_SEC = 20.0


def grade(source: str, task: dict[str, str], workspace: Any) -> tuple[bool, str]:
    """Run one candidate solution and compare its output to the expectation.

    Args:
        source: The candidate's Python source, defining ``solve``.
        task: The task row (``call``, ``expect``).
        workspace: Directory to write the solution into — from
            :meth:`chimera.experiments.Run.subdir`, so it is inside the run.

    Returns:
        ``(passed, detail)``. *detail* is the observed output, or the error
        that stopped it — recorded either way, because an unexplained miss is
        indistinguishable from a broken harness.
    """
    solution = workspace / "solution.py"
    solution.write_text(f"{source}\nprint({task['call']})\n", encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, str(solution)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {TIMEOUT_SEC:.0f}s"
    if proc.returncode != 0:
        return False, (proc.stderr.strip().splitlines() or ["nonzero exit"])[-1]
    got = proc.stdout.strip()
    return got == task["expect"], got


def main(argv: list[str] | None = None) -> int:
    """Run (or resume) the sweep and write its receipt.

    Args:
        argv: Command-line arguments; ``None`` reads ``sys.argv``.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--limit", type=int, default=len(TASKS), help="tasks per solver"
    )
    parser.add_argument(
        "--crash-after",
        type=int,
        default=0,
        help="abort hard after N units, to demonstrate crash-safety and resume",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="start a new run instead of resuming an interrupted one",
    )
    args = parser.parse_args(argv)

    tasks = TASKS[: max(0, args.limit)]
    config = {
        "solvers": sorted(SOLVERS),
        "limit": len(tasks),
        "grader": "subprocess-stdout-compare",
    }

    # One call replaces the driver's whole preamble: the run directory, the
    # manifest, the git SHA + dirty flag, and — with resume=True — reattaching
    # to whatever the last crash left behind.
    run = start(EXPERIMENT, config=config, resume=not args.fresh)
    done = run.seen("progress.jsonl", key="unit")
    if done:
        print(f"resuming {run.name}/{run.stamp}, {len(done)} unit(s) already done")
    else:
        print(f"starting {run.name}/{run.stamp}")

    completed = 0
    for solver, sources in sorted(SOLVERS.items()):
        for task in tasks:
            unit = f"{solver}/{task['id']}"
            if unit in done:
                continue
            workspace = run.subdir(f"ws/{solver}/{task['id']}")
            passed, detail = grade(sources[task["id"]], task, workspace)
            # One flushed row per unit of work — the resume ledger.
            run.jsonl(
                "progress.jsonl",
                {
                    "unit": unit,
                    "solver": solver,
                    "task": task["id"],
                    "passed": passed,
                    "detail": detail,
                    "cost_usd": 0.0,  # offline: no model was called
                },
            )
            print(f"  {unit}: {'PASS' if passed else 'FAIL'} ({detail})")
            completed += 1
            if args.crash_after and completed >= args.crash_after:
                print(f"  -- aborting after {completed} unit(s), as requested --")
                sys.stdout.flush()
                os._exit(9)  # no cleanup: exactly what a real crash looks like

    rows = run.rows("progress.jsonl")
    cells = [
        {
            "agent_id": solver,
            "benchmark": "example-tasks",
            "total": len([r for r in rows if r["solver"] == solver]),
            "passed": len([r for r in rows if r["solver"] == solver and r["passed"]]),
            "cost_usd": 0.0,
            "status": "completed",
        }
        for solver in sorted(SOLVERS)
    ]
    receipt = run.finish({"cells": cells})

    for cell in cells:
        print(f"{cell['agent_id']}: {cell['passed']}/{cell['total']}")
    print(f"receipt: {receipt}")
    print(f"inspect: chimera experiments show {EXPERIMENT}/{run.stamp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
