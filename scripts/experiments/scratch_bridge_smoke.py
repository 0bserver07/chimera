"""Live smoke of the PRODUCTIONIZED ProgramBench.rebuild_instance via the bridge.

Reuses already-extracted _inputs (no docker pull/extract) and a real qwen3-coder
codegen + real grading. Confirms the wired path end-to-end. Delete after use.
"""
import os
from pathlib import Path

from chimera.config.paths import store_path

for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
os.environ["ANTHROPIC_BASE_URL"] = "http://localhost:11434"
os.environ["ANTHROPIC_API_KEY"] = os.environ.get("OLLAMA_API_KEY", "")
os.environ["ANTHROPIC_AUTH_TOKEN"] = os.environ.get("OLLAMA_API_KEY", "")
os.environ["CHIMERA_PROGRAMBENCH_LIVE"] = "1"

from chimera.eval.benchmarks.programbench import ProgramBench  # noqa: E402
from chimera.providers.factory import create_provider  # noqa: E402

# Chimera-owned experiment-state root (scripts/experiments/README.md): history
# and new runs live OUTSIDE the repo, under ~/.chimera — never at the repo
# root, which is gated (tests/test_repo_hygiene.py). Override for relocation.
PB_RUNS = Path(
    os.environ.get("CHIMERA_PB_RUNS")
    or store_path("experiment-runs") / "pb-runs"
)

TASKS = "/Users/yadkonrad/dev_dev/year26/may26/ProgramBench/src/programbench/data/tasks"
PB_CLI = ("/Users/yadkonrad/dev_dev/year26/may26/ProgramBench/.venv/bin/programbench", "eval")
INSTANCE = "agourlay__zip-password-finder.704700d"
WS = PB_RUNS / f"2026-06-17-sweep/glm-5.2_cloud/{INSTANCE}/ws"  # has _inputs

bench = ProgramBench(tasks_dir=TASKS, run_dir=str(PB_RUNS / "_live_rebuild"), programbench_cli=PB_CLI)
task = next(t for t in bench.tasks() if (t.get("instance_id") or t.get("id")) == INSTANCE)
provider = create_provider(model="qwen3-coder-next:cloud")


def _log(a):
    print(f"[attempt {a.index}] files={a.files} resolved={a.resolved} summary={a.summary}", flush=True)


result = bench.rebuild_instance(
    task, provider=provider, workspace=WS, max_repair=1,
    pull_image=False, extract_artifacts=False, runtime_check=False, on_attempt=_log,
)
print(f"RESOLVED={result.resolved} | best={result.best_summary} | files={sorted(result.files)}", flush=True)
