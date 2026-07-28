"""ProgramBench agentic sweep via the real chimera code CodingAgent.

- run_instance's docker pull HANGS (images built locally, not pullable) -> we
  pre-seed each workspace's _inputs from the June sweep and pass
  pull_image=False + extract_artifacts=False.
- The CodingAgent runs `cargo build` etc., so the workspace fills with build
  artifacts (target/), its own .chimera/ session state, and a locally-compiled
  executable. The default packager tars ALL of that -> the grader chokes and
  emits no eval.json. clean_packager below ships ONLY source.
- Grade via the fixed evaluate() (the programbench eval CLI builds the grading
  image itself). Per-task progress -> JSONL (resumable).

    ANTHROPIC_BASE_URL=... ANTHROPIC_API_KEY=... ANTHROPIC_AUTH_TOKEN=... \
    PB_INSTANCE=<id> PB_LIMIT=1 uv run python <this>/pb_agentic.py
"""
import json
import os
import shutil
import tarfile
from pathlib import Path

for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
os.environ["CHIMERA_PROGRAMBENCH_LIVE"] = "1"

from chimera.eval.benchmarks.programbench import ProgramBench  # noqa: E402
from chimera.eval.coding_agent_adapter import CodingAgentAdapter  # noqa: E402
from chimera.providers.anthropic import AnthropicProvider  # noqa: E402

TASKS = "/Users/yadkonrad/dev_dev/year26/may26/ProgramBench/src/programbench/data/tasks"
PB_CLI = ("/Users/yadkonrad/dev_dev/year26/may26/ProgramBench/.venv/bin/programbench", "eval")
# Chimera-owned experiment-state root (scripts/experiments/README.md): history
# and new runs live OUTSIDE the repo, under ~/.chimera — never at the repo
# root, which is gated (tests/test_repo_hygiene.py). Override for relocation.
PB_RUNS = Path(
    os.environ.get("CHIMERA_PB_RUNS")
    or Path.home() / ".chimera" / "experiment-runs" / "pb-runs"
)

RUN_DIR = str(PB_RUNS / "_agentic/run")
SWEEP = PB_RUNS / "2026-06-17-sweep/glm-5.2_cloud"
MODEL = os.environ.get("PB_MODEL", "glm-5.2[1m]")
PROGRESS = PB_RUNS / "_agentic/progress.jsonl"
PROGRESS.parent.mkdir(parents=True, exist_ok=True)

# Exclude build artifacts, the agent's own state, and the local binary so the
# grader receives only source (matches the clean submissions that grade).
_EXCLUDE = {"target", ".chimera", "_inputs", "executable", ".git", "__pycache__",
            "node_modules", "build", "dist", "submission.tar.gz", ".pytest_cache",
            ".mypy_cache", ".ruff_cache", ".rustc_info.json"}


def clean_packager(ws: Path, out_tar: Path) -> None:
    def _keep(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
        return None if set(ti.name.split("/")) & _EXCLUDE else ti
    with tarfile.open(out_tar, "w:gz") as tf:
        for item in sorted(ws.iterdir()):
            if item.name in _EXCLUDE:
                continue
            tf.add(item, arcname=item.name, filter=_keep)


avail = {
    d.name for d in SWEEP.iterdir() if (d / "ws" / "_inputs").is_dir()
} if SWEEP.exists() else set()

pb = ProgramBench(tasks_dir=TASKS, run_dir=RUN_DIR, programbench_cli=PB_CLI)
provider = AnthropicProvider(model=MODEL)

done: set[str] = set()
if PROGRESS.exists():
    for ln in PROGRESS.read_text().splitlines():
        if ln.strip():
            done.add(json.loads(ln)["instance_id"])

only = os.environ.get("PB_INSTANCE", "")
if only:
    tasks = [t for t in pb.tasks() if (t.get("instance_id") or t.get("id")) == only]
else:
    tasks = [t for t in pb.tasks() if (t.get("instance_id") or t.get("id")) in avail]
LIMIT = int(os.environ.get("PB_LIMIT", str(len(tasks))))
tasks = tasks[:LIMIT]
print(f"agentic sweep: {len(tasks)} task(s), model={MODEL}, {len(done)} done", flush=True)

for i, task in enumerate(tasks):
    iid = task.get("instance_id") or task.get("id") or str(i)
    if iid in done:
        print(f"[{i}] skip {iid} (done)", flush=True)
        continue
    src_inputs = SWEEP / iid / "ws" / "_inputs"
    ws = PB_RUNS / f"_agentic/ws/{iid}"
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    if src_inputs.is_dir():
        shutil.copytree(src_inputs, ws / "_inputs",
                        ignore=shutil.ignore_patterns("executable", "*.o", "*.a"))
        extract = False  # reuse pre-extracted inputs
    else:
        extract = True   # extract from a locally-present cleanroom image
    adapter = CodingAgentAdapter(provider=provider)
    try:
        rr = pb.run_instance(
            task, agent=adapter, workspace=str(ws),
            pull_image=False, extract_artifacts=extract,
            submission_packager=clean_packager,
        )
        passed = pb.evaluate(task, str(rr.submission_tar))
        rec = {"instance_id": iid, "passed": bool(passed),
               "cost": round(rr.cost or 0.0, 5), "steps": rr.steps, "error": rr.error}
    except Exception as exc:  # noqa: BLE001
        rec = {"instance_id": iid, "passed": False, "cost": 0.0, "steps": 0,
               "error": f"{type(exc).__name__}: {exc}"}
    with open(PROGRESS, "a") as f:
        f.write(json.dumps(rec) + "\n")
        f.flush()
    print(f"[{i}] {iid}: passed={rec['passed']} cost=${rec['cost']:.4f} "
          f"steps={rec['steps']} err={rec['error']}", flush=True)

recs = [json.loads(ln) for ln in PROGRESS.read_text().splitlines() if ln.strip()]
p = sum(1 for r in recs if r["passed"])
c = sum((r.get("cost") or 0.0) for r in recs)
print(f"SUMMARY: {p}/{len(recs)} passed, ${c:.4f} total", flush=True)
