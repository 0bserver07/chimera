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

_REPO = Path(__file__).resolve().parent.parent
_CHIMERA_PKG = _REPO / "chimera"
_DATASETS = Path.home() / ".chimera" / "datasets"

# Chimera (local source, with the Modal fixes) + staged datasets baked in.
# HOME=/root so the benches' ~/.chimera/datasets lookup resolves to the copy.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("anthropic", "httpx")
    .env({"PYTHONPATH": "/pkg", "HOME": "/root"})
    .add_local_dir(str(_CHIMERA_PKG), "/pkg/chimera", copy=True)
    .add_local_dir(str(_DATASETS), "/root/.chimera/datasets", copy=True)
)

app = modal.App("chimera-bench")


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
    timeout=1800,
)
def run_cell_cpu(
    agent: str, bench: str, limit: int, model: str, max_tool_calls: int, max_cost: float
) -> dict:
    return _run_one(agent, bench, limit, model, max_tool_calls, max_cost)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("chimera-glm")],
    timeout=1800,
    gpu="T4",
)
def run_cell_gpu(
    agent: str, bench: str, limit: int, model: str, max_tool_calls: int, max_cost: float
) -> dict:
    return _run_one(agent, bench, limit, model, max_tool_calls, max_cost)


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
