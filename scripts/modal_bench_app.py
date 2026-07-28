"""Run Chimera benchmark cells entirely on Modal — the whole grid in the cloud.

Unlike ``bench-matrix --env modal`` (which runs the orchestration locally and
only the per-task *execution* in a Modal sandbox), this runs EVERYTHING inside
a Modal function: orchestration + model inference (via the ``chimera-glm``
secret) + task execution + grading. Fire one command and the cell runs on
Modal's compute — optionally on a GPU.

Usage:
    # one cell, CPU:
    modal run scripts/modal_bench_app.py --agent react --bench mbpp --limit 2
    # on a GPU:
    modal run scripts/modal_bench_app.py --agent coding-agent --bench mbpp --limit 5 --gpu T4

Prereqs (already set up in this workspace):
    - Modal auth (~/.modal.toml, workspace 0bserver07)
    - `modal secret create chimera-glm ANTHROPIC_API_KEY=… ANTHROPIC_BASE_URL=… ANTHROPIC_MODEL=…`
"""

from __future__ import annotations

import json
from pathlib import Path

import modal
from chimera.config.paths import store_path

_REPO = Path(__file__).resolve().parent.parent
_CHIMERA_PKG = _REPO / "chimera"
_DATASETS = store_path("datasets")
#: Where the local entrypoints drop their grid receipts. Anchored on the repo
#: rather than the cwd: ``modal run`` can be fired from anywhere, and a
#: cwd-relative ``data/`` scatters a stray directory wherever you were standing
#: (``tests/test_repo_hygiene.py`` gates exactly that shape). Promotion of a
#: receipt into the curated set is still a deliberate act, not this write.
_RECEIPTS = _REPO / "data"

# Chimera (local source, with the Modal fixes) + staged datasets baked in.
# HOME=/root so the benches' ~/.chimera/datasets lookup resolves to the copy.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("anthropic", "httpx", "datasets")
    .env({"PYTHONPATH": "/pkg", "HOME": "/root"})
    .add_local_dir(str(_CHIMERA_PKG), "/pkg/chimera", copy=True)
    .add_local_dir(str(_DATASETS), "/root/.chimera/datasets", copy=True)
)

app = modal.App("chimera-bench")

# Cap how many cells run at once. Modal can fan out to hundreds of containers,
# but every cell drives the SAME single model account (the chimera-glm secret) —
# so unbounded fan-out is dozens of concurrent LLM streams against one account's
# rate limit, which collapses into mass errors. Keep this at the account's safe
# concurrency (a handful), NOT Modal's max. Raise it only if the account's rate
# limit genuinely allows more.
_MAX_CONCURRENCY = 4

# Durable results (playbook 13, learned the hard way): a plain `modal run`
# keeps cells alive only while the LOCAL client stays connected — a laptop
# sleeping mid-run had Modal terminate every in-flight cell ("local client
# disconnected"), losing a 2h full-dataset run. Cells therefore persist their
# own result JSON to this Volume the moment they finish; `::grid_detached`
# (fired with `modal run --detach`) spawns cells and exits, and `::collect`
# reads the Volume later. Completed work survives any client death.
results_volume = modal.Volume.from_name("chimera-bench-results", create_if_missing=True)
_RESULTS_DIR = "/results"


def _run_one(
    agent: str, bench: str, limit: int, model: str, max_tool_calls: int, max_cost: float
) -> dict:
    """Run a single agent × benchmark cell in-process and return the report dict.

    Runs inside the Modal function: the ``chimera-glm`` secret supplies
    ANTHROPIC_* so ``create_provider`` reaches the model, and tasks execute in
    the function's own container (env=local).
    """
    import tempfile

    from chimera.cli.bench_matrix import _report_to_dict
    from chimera.cli.main import _load_benchmark
    from chimera.core.budget import BudgetSpec
    from chimera.env.local import LocalEnvironment
    from chimera.eval.matrix import run_matrix
    from chimera.eval.runners.registry import load_registry, resolve
    from chimera.providers.factory import create_provider

    registry = load_registry(None)
    if agent not in registry:
        raise ValueError(f"unknown agent {agent!r}; have {sorted(registry)}")
    provider = create_provider(model=model)
    runner = resolve(registry[agent], provider=provider)
    benchmark = _load_benchmark(bench, dataset=None, limit=limit)
    budget = BudgetSpec(
        max_tool_calls=max_tool_calls, max_llm_calls=max_tool_calls, max_cost_usd=max_cost
    )

    def env_factory() -> LocalEnvironment:
        return LocalEnvironment(workdir=tempfile.mkdtemp(prefix="chimera-modal-"))

    report = run_matrix([runner], [benchmark], env_factory=env_factory, budget=budget, model=model)
    return _report_to_dict(report)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("chimera-glm")],
    # Sized from measured per-task time (playbook 13 rule 4): the flagship runs
    # ~46.5s/task, so a full mbpp column (427 tasks) needs ~5.5h; 12h covers
    # every full-dataset column with headroom. Per-task budget caps still bound
    # the spend — the timeout bounds only the wall clock.
    timeout=43200,
    max_containers=_MAX_CONCURRENCY,
)
def run_cell_cpu(
    agent: str, bench: str, limit: int, model: str, max_tool_calls: int, max_cost: float
) -> dict:
    return _run_one(agent, bench, limit, model, max_tool_calls, max_cost)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("chimera-glm")],
    timeout=3600,
    gpu="T4",
    max_containers=_MAX_CONCURRENCY,
)
def run_cell_gpu(
    agent: str, bench: str, limit: int, model: str, max_tool_calls: int, max_cost: float
) -> dict:
    return _run_one(agent, bench, limit, model, max_tool_calls, max_cost)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("chimera-glm")],
    timeout=43200,
    max_containers=_MAX_CONCURRENCY,
    volumes={_RESULTS_DIR: results_volume},
)
def run_cell_durable(
    run_id: str,
    agent: str,
    bench: str,
    limit: int,
    model: str,
    max_tool_calls: int,
    max_cost: float,
) -> str:
    """Run a cell and persist its result to the Volume — survives client death.

    Writes ``/results/<run_id>/<agent>__<bench>.json`` (a cell error is written
    too, as ``{"error": ...}``) and commits the Volume, so a detached run's
    completed cells are durable even if the spawning client is long gone.
    Returns the volume-relative path.
    """
    import json as _json
    import os as _os

    out_dir = f"{_RESULTS_DIR}/{run_id}"
    _os.makedirs(out_dir, exist_ok=True)
    path = f"{out_dir}/{agent}__{bench}.json"
    try:
        result = _run_one(agent, bench, limit, model, max_tool_calls, max_cost)
    except Exception as exc:  # noqa: BLE001 — persist the failure, don't lose it
        result = {"agent": agent, "bench": bench, "error": f"{type(exc).__name__}: {exc}"}
    with open(path, "w") as fh:
        _json.dump(result, fh)
    results_volume.commit()
    return path.removeprefix(_RESULTS_DIR + "/")


@app.local_entrypoint()
def main(
    agent: str = "react",
    bench: str = "mbpp",
    limit: int = 2,
    model: str = "glm-5.2[1m]",
    max_tool_calls: int = 10,
    max_cost: float = 0.15,
    gpu: str = "",
) -> None:
    fn = run_cell_gpu if gpu else run_cell_cpu
    where = f"GPU={gpu}" if gpu else "CPU"
    print(f"[chimera-bench on Modal] {agent} x {bench} (n={limit}, {where})...")
    result = fn.remote(agent, bench, limit, model, max_tool_calls, max_cost)
    cells = result.get("cells", [])
    for c in cells:
        print(
            f"  {c['agent_id']} x {c['benchmark']}: "
            f"{c['passed']}/{c['total']} ({c.get('pass_rate', 0):.0%})  "
            f"${c.get('cost_usd', 0):.4f}  status={c.get('status')}"
        )
    print(json.dumps(result, indent=2))


@app.local_entrypoint()
def grid(
    agents: str = "coding-agent,react,reflexion,tree-of-thought",
    benches: str = "mbpp,livecodebench",
    limit: int = 5,
    model: str = "glm-5.2[1m]",
    max_tool_calls: int = 15,
    max_cost: float = 0.15,
    gpu: str = "",
) -> None:
    """Fan an agents×benches GRID out as CONCURRENT Modal functions.

    Every cell runs in its own Modal container in parallel (``.starmap``), so
    wall-clock ≈ the slowest single cell — not the serial sum. This is the fix
    for the local depth-run timeouts.
    """
    from datetime import datetime

    agent_list = [a.strip() for a in agents.split(",") if a.strip()]
    bench_list = [b.strip() for b in benches.split(",") if b.strip()]
    cells = [
        (a, b, limit, model, max_tool_calls, max_cost)
        for a in agent_list
        for b in bench_list
    ]
    fn = run_cell_gpu if gpu else run_cell_cpu
    where = f"GPU={gpu}" if gpu else "CPU"
    print(
        f"[chimera-bench grid] {len(agent_list)} agents x {len(bench_list)} benches "
        f"= {len(cells)} cells, PARALLEL on Modal ({where})..."
    )

    # return_exceptions: one bad cell surfaces as an error, never sinks the grid.
    # wrap_returned_exceptions=False → raw exceptions (Modal's own, e.g.
    # FunctionTimeoutError from a preempted+restarted long cell) so the type is
    # legible, not an empty-message wrapper.
    results = list(fn.starmap(cells, return_exceptions=True, wrap_returned_exceptions=False))

    combined: list[dict] = []
    for (a, b, *_), res in zip(cells, results):
        if isinstance(res, BaseException):
            combined.append(
                {"agent_id": a, "benchmark": b, "passed": 0, "total": 0,
                 "pass_rate": 0.0, "status": "error",
                 "error": f"{type(res).__name__}: {str(res)[:150]}"}
            )
        else:
            combined.extend(res.get("cells", []))

    # Pass-rate table: agents (rows) × benches (cols).
    benches_seen = sorted({c["benchmark"] for c in combined})
    by_cell = {(c["agent_id"], c["benchmark"]): c for c in combined}
    col_w = max((len(b) for b in benches_seen), default=6)
    print("\n" + " " * 18 + "".join(f"{b:>{col_w + 2}}" for b in benches_seen))
    for a in agent_list:
        row = f"{a:<18}"
        for b in benches_seen:
            c = by_cell.get((a, b)) or next(
                (x for x in combined if x["agent_id"] == a and x["benchmark"].startswith(b)),
                None,
            )
            cellstr = f"{c['passed']}/{c['total']}" if c and c.get("total") else (
                "err" if c and c["status"] == "error" else "-")
            row += f"{cellstr:>{col_w + 2}}"
        print(row)

    total_cost = sum(float(c.get("cost_usd", 0) or 0) for c in combined)
    errs = sum(1 for c in combined if c.get("status") == "error")
    print(f"\ncells: {len(combined)} | errors: {errs} | total cost: ${total_cost:.4f}")

    _RECEIPTS.mkdir(parents=True, exist_ok=True)
    out = _RECEIPTS / f"modal-grid-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    with out.open("w") as fh:
        json.dump({"model": model, "cells": combined}, fh, indent=2)
    print(f"saved -> {out}")


@app.local_entrypoint()
def grid_detached(
    run_id: str,
    agents: str = "coding-agent",
    benches: str = "mbpp",
    limit: int = 500,
    model: str = "glm-5.2[1m]",
    max_tool_calls: int = 15,
    max_cost: float = 0.15,
) -> None:
    """Spawn a grid DETACHED — cells outlive this client and persist to a Volume.

    Fire with ``modal run --detach scripts/modal_bench_app.py::grid_detached
    --run-id <id> ...``: this spawns every cell (still throttled by
    ``max_containers``) and exits immediately. Each cell writes its result to
    the ``chimera-bench-results`` Volume under ``<run_id>/``. Fetch later with
    ``::collect --run-id <id>`` — a sleeping laptop can no longer kill the run.
    """
    agent_list = [a.strip() for a in agents.split(",") if a.strip()]
    bench_list = [b.strip() for b in benches.split(",") if b.strip()]
    cells = [(a, b) for a in agent_list for b in bench_list]
    print(
        f"[grid_detached run_id={run_id}] spawning {len(cells)} cells "
        f"(throttle {_MAX_CONCURRENCY}); safe to disconnect after this exits."
    )
    for a, b in cells:
        call = run_cell_durable.spawn(run_id, a, b, limit, model, max_tool_calls, max_cost)
        print(f"  spawned {a} x {b} -> {call.object_id}")
    print(f"collect with: modal run scripts/modal_bench_app.py::collect --run-id {run_id}")


@app.local_entrypoint()
def collect(run_id: str) -> None:
    """Collect a detached run's cells from the Volume into data/ + a table."""
    import io

    # NOTE: no reload() — that API is container-only ("can only be called from
    # within a running function"); a local entrypoint's listdir/read_file hit
    # the Volume service directly and always see committed state.
    combined: list[dict] = []
    expected: list[str] = []
    for entry in results_volume.listdir(f"/{run_id}"):
        name = entry.path.split("/")[-1]
        expected.append(name)
        buf = io.BytesIO()
        for chunk in results_volume.read_file(f"{run_id}/{name}"):
            buf.write(chunk)
        payload = json.loads(buf.getvalue())
        if "cells" in payload:
            combined.extend(payload["cells"])
        else:  # persisted cell-level error
            combined.append(
                {"agent_id": payload.get("agent", "?"), "benchmark": payload.get("bench", "?"),
                 "passed": 0, "total": 0, "pass_rate": 0.0, "status": "error",
                 "error": payload.get("error", "")}
            )
    if not combined:
        print(f"[collect {run_id}] no cells on the volume yet — still running?")
        return
    print(f"[collect {run_id}] {len(expected)} cell files:")
    for c in combined:
        n = f"{c['passed']}/{c['total']}" if c.get("total") else c.get("status", "?")
        print(f"  {c['agent_id']:<16} {c['benchmark'].split(':')[0]:<28} {n:>8}  "
              f"${float(c.get('cost_usd', 0) or 0):.3f}  {c.get('status','')}")
    from datetime import datetime

    _RECEIPTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = _RECEIPTS / f"modal-grid-{run_id}-{stamp}.json"
    with out.open("w") as fh:
        json.dump({"run_id": run_id, "cells": combined}, fh, indent=2)
    print(f"saved -> {out}")
