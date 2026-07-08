"""``chimera bench-modal`` — run benchmarks on Modal cloud as a first-class CLI.

A thin, robust wrapper over the deployable Modal app
(``scripts/modal_bench_app.py``): it shells out to ``modal run`` so the whole
Modal lifecycle (image build, secret injection, parallel fan-out) is handled by
Modal itself. Runs an agents×benches GRID by default (concurrent cells); pass a
single ``--agent``/``--bench`` for one cell.

Requires: the ``modal`` CLI + Modal auth (``modal setup``), and the
``chimera-glm`` secret for cloud inference. See
``docs/benchmarks/modal-cloud-benches.md``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def add_bench_modal_parser(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """Register the ``bench-modal`` subcommand."""
    p = subparsers.add_parser(
        "bench-modal",
        help="Run benches on Modal cloud — parallel agents×benches grid (or one cell)",
    )
    p.add_argument(
        "--agents",
        default="coding-agent,react",
        help="Comma-separated agent ids (grid rows)",
    )
    p.add_argument(
        "--benches",
        default="mbpp",
        help="Comma-separated benchmark names (grid columns)",
    )
    p.add_argument("--limit", type=int, default=5, help="Tasks per cell")
    p.add_argument("--model", default="glm-5.2[1m]", help="Model shared by every cell")
    p.add_argument(
        "--gpu",
        default="",
        help="Modal GPU per cell (e.g. T4, A100). Omit for CPU.",
    )
    p.add_argument("--max-tool-calls", type=int, default=15, help="Per-task tool-call budget")
    p.add_argument("--max-cost", type=float, default=0.15, help="Per-task cost budget ($)")


def _modal_app_path() -> Path | None:
    """Locate the deployable Modal app script within a Chimera checkout."""
    import chimera

    candidate = Path(chimera.__file__).resolve().parent.parent / "scripts" / "modal_bench_app.py"
    return candidate if candidate.exists() else None


def build_modal_command(args: argparse.Namespace, app_path: Path) -> list[str]:
    """Build the ``modal run …::grid`` argv from parsed CLI args (pure/testable)."""
    cmd = [
        "modal", "run", f"{app_path}::grid",
        "--agents", args.agents,
        "--benches", args.benches,
        "--limit", str(args.limit),
        "--model", args.model,
        "--max-tool-calls", str(args.max_tool_calls),
        "--max-cost", str(args.max_cost),
    ]
    if args.gpu:
        cmd += ["--gpu", args.gpu]
    return cmd


def run_bench_modal(args: argparse.Namespace) -> int:
    """Dispatch ``chimera bench-modal`` → ``modal run …::grid``."""
    if shutil.which("modal") is None:
        print(
            "chimera bench-modal: the `modal` CLI is not installed. "
            "`pip install modal` then `modal setup`.",
            file=sys.stderr,
        )
        return 2
    app_path = _modal_app_path()
    if app_path is None:
        print(
            "chimera bench-modal: Modal app script not found. This command runs "
            "from a Chimera source checkout (scripts/modal_bench_app.py).",
            file=sys.stderr,
        )
        return 2
    cmd = build_modal_command(args, app_path)
    print(f"[bench-modal] {' '.join(cmd)}", file=sys.stderr)
    return subprocess.call(cmd)
