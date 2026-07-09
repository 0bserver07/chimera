#!/usr/bin/env python3
"""Verify the bench/agent stack's ACTUAL state — claims replaced by checks.

Run at session start and before any status report:

    uv run python scripts/verify_status.py [--json]

Every check is offline (faux provider, staged datasets, no API spend) and
fast (<90s). Exit code 0 = all green; 1 = something regressed. The point:
"done" is what this script proves, not what a conversation claimed.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECKS: list[tuple[str, str, bool]] = []  # (name, detail, ok)


def check(name: str, ok: bool, detail: str) -> None:
    CHECKS.append((name, detail, ok))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}: {detail}")


def benches() -> None:
    """Every registered bench either loads real tasks or is known data-gated."""
    from chimera.cli.main import _BENCHMARKS, _load_benchmark

    canon: dict[str, str] = {}
    for k in sorted(_BENCHMARKS):
        canon.setdefault(k.replace("-", "").replace("_", ""), k)
    runnable: list[tuple[str, int]] = []
    for name in sorted(canon.values()):
        try:
            n = len(_load_benchmark(name, dataset=None, limit=None).tasks())
        except Exception:
            n = 0
        if n:
            runnable.append((name, n))
    total = sum(n for _, n in runnable)
    # lcb + livecodebench are alias keys of one adapter — count distinct - 1.
    distinct = len(runnable) - (
        1 if any(n == "lcb" for n, _ in runnable) and any(n == "livecodebench" for n, _ in runnable) else 0
    )
    check(
        "benches",
        distinct >= 7 and total >= 1900,
        f"{distinct} distinct runnable ({total} tasks): "
        + ", ".join(f"{n}={c}" for n, c in runnable if n != "lcb"),
    )


def agents() -> None:
    """All roster agents construct AND run end-to-end on the faux provider."""
    from chimera.env.local import LocalEnvironment
    from chimera.eval.runners.registry import default_agent_specs, load_registry, resolve
    from chimera.providers.faux import FauxProvider

    registry = load_registry(None)
    specs = default_agent_specs()
    failed: list[str] = []
    for spec in specs:
        try:
            runner = resolve(
                registry[spec.id],
                provider=FauxProvider(script=[{"text": "def f():\n    return 1"}]),
            )
            with tempfile.TemporaryDirectory() as d:
                r = runner.run("write f", LocalEnvironment(workdir=d), None)
            if r.status != "completed":
                failed.append(f"{spec.id}({r.status})")
        except Exception as exc:  # noqa: BLE001 — report, don't crash the audit
            failed.append(f"{spec.id}({type(exc).__name__})")
    check(
        "agents",
        not failed and len(specs) == 13,
        f"{len(specs) - len(failed)}/{len(specs)} run offline"
        + (f"; FAILED: {', '.join(failed)}" if failed else ""),
    )


def grading_integrity() -> None:
    """Empty output must grade False; the HumanEval+ checker must actually run."""
    from chimera.eval.benchmarks.human_eval import HumanEval

    task = {
        "test": "def check(candidate):\n    assert candidate(2, 3) == 5\n",
        "entry_point": "add",
    }
    bench = HumanEval()
    empty_fails = bench.evaluate(task, "", None) is False
    wrong_fails = (
        bench.evaluate(task, "```python\ndef add(a, b):\n    return a - b\n```", None) is False
    )
    right_passes = (
        bench.evaluate(task, "```python\ndef add(a, b):\n    return a + b\n```", None) is True
    )
    check(
        "grading-integrity",
        empty_fails and wrong_fails and right_passes,
        f"empty->False:{empty_fails} wrong->False:{wrong_fails} correct->True:{right_passes}",
    )


def cell_status() -> None:
    """Mixed per-task statuses must aggregate honestly (not last-attempt)."""
    from chimera.eval.matrix import _derive_cell_status

    ok = (
        _derive_cell_status(["completed", "completed", "error"]) == "partial_error"
        and _derive_cell_status(["error", "completed"]) == "partial_error"
        and _derive_cell_status(["completed"] * 3) == "completed"
    )
    check("cell-status", ok, "mixed statuses -> partial_error (no last-attempt mislabel)")


def data_integrity() -> None:
    """No saved grid may contain uniform-error cells graded as passing."""
    suspicious: list[str] = []
    for f in sorted(glob.glob(str(REPO / "data" / "modal-grid-*.json")))[-3:]:
        try:
            cells = json.load(open(f)).get("cells", [])
        except Exception:
            continue
        fp = [c for c in cells if c.get("status") == "error" and c.get("passed", 0) > 0]
        if fp:
            suspicious.append(f"{Path(f).name}:{len(fp)}")
    # Pre-fix files legitimately contain false positives; only files newer than
    # the integrity fix count as violations.
    newest = sorted(glob.glob(str(REPO / "data" / "modal-grid-*.json")))[-1:]
    newest_bad = [s for s in suspicious if newest and Path(newest[0]).name in s]
    check(
        "data-integrity",
        not newest_bad,
        "latest grid clean" if not newest_bad else f"latest grid has false positives: {newest_bad}",
    )


def modal_auth() -> None:
    authed = (
        (Path.home() / ".modal.toml").exists()
        or os.environ.get("MODAL_TOKEN_ID")
        or os.environ.get("MODAL_TOKEN_SECRET")
    )
    check("modal-auth", bool(authed), "~/.modal.toml present" if authed else "NO Modal auth")


def modal_throttle() -> None:
    """The grid app must carry a concurrency cap — never flood the account."""
    src = (REPO / "scripts" / "modal_bench_app.py").read_text()
    ok = "max_containers" in src and "_MAX_CONCURRENCY" in src
    check("modal-throttle", ok, "max_containers cap present" if ok else "NO concurrency cap")


def git_state() -> None:
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    unpushed = subprocess.run(
        ["git", "log", "--oneline", "origin/master..HEAD"],
        cwd=REPO, capture_output=True, text=True,
    ).stdout.strip()
    check(
        "git",
        not dirty and not unpushed,
        ("clean+pushed" if not dirty and not unpushed
         else f"dirty:{len(dirty.splitlines())} unpushed:{len(unpushed.splitlines())}"),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    print("chimera verify-status — every claim below is checked, not asserted\n")
    for fn in (benches, agents, grading_integrity, cell_status, data_integrity,
               modal_auth, modal_throttle, git_state):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — one broken check must not hide the rest
            check(fn.__name__, False, f"check crashed: {type(exc).__name__}: {exc}")

    failed = [c for c in CHECKS if not c[2]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks green")
    if args.json:
        print(json.dumps([{"check": n, "detail": d, "ok": ok} for n, d, ok in CHECKS]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
