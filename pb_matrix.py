"""ProgramBench comparative matrix driver: one-shot+compile-repair (+ RAG)
across tasks x models, via the Ollama-Cloud bridge.

Sequential by design — grading (docker/QEMU on this arm64 host) is the
bottleneck and can't parallelize on one daemon; on a real linux/amd64 cloud
this becomes an embarrassingly-parallel Workflow.

Config via env:
  PB_MODELS     comma list (default: qwen3-coder-next:cloud,glm-5.2:cloud)
  PB_LANG       language filter (default: c — faster compiles than rust)
  PB_LIMIT      tasks (default: 8)
  PB_MAX_REPAIR repair rounds (default: 3)
  PB_RAG        1 to enable DocsRsProvider (default: 1)
  PB_SKIP       comma list of instance_ids to skip (e.g. interactive TUIs)

Writes pb-runs/_matrix/<stamp>/matrix.jsonl (flushed) + a printed summary.
NOTE: interactive programs (cmatrix, TUIs) hang grading — keep them in PB_SKIP
until a per-grade timeout lands. Run attended; watch the first task.
"""
import json
import os
from pathlib import Path

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
from chimera.eval.benchmarks.rebuild_docs import DocsRsProvider  # noqa: E402
from chimera.providers.factory import create_provider  # noqa: E402

TASKS = "/Users/yadkonrad/dev_dev/year26/may26/ProgramBench/src/programbench/data/tasks"
PB_CLI = ("/Users/yadkonrad/dev_dev/year26/may26/ProgramBench/.venv/bin/programbench", "eval")
MODELS = os.environ.get("PB_MODELS", "qwen3-coder-next:cloud,glm-5.2:cloud").split(",")
LANGS = [s for s in os.environ.get("PB_LANGS", "c,go").split(",") if s]
DIFFICULTY = os.environ.get("PB_DIFFICULTY", "easy")
LIMIT = int(os.environ.get("PB_LIMIT", "0"))  # 0 = all matching
MAX_REPAIR = int(os.environ.get("PB_MAX_REPAIR", "3"))
RAG = os.environ.get("PB_RAG", "1") == "1"
# Skip interactive programs (ncurses animations) — they hang grading.
SKIP = {s for s in os.environ.get(
    "PB_SKIP", "abishekvashok__cmatrix.5c082c6,xorg62__tty-clock.f2f847c"
).split(",") if s}

OUT = Path("pb-runs/_matrix/run")
OUT.mkdir(parents=True, exist_ok=True)
logf = open(OUT / "matrix.jsonl", "a")  # noqa: SIM115
docs = DocsRsProvider() if RAG else None


def emit(rec):
    line = json.dumps(rec)
    print(line, flush=True)
    logf.write(line + "\n")
    logf.flush()


tasks = []
for lang in LANGS:
    cat = ProgramBench(tasks_dir=TASKS, language=lang, difficulty=DIFFICULTY)
    for t in cat.tasks():
        if (t.get("instance_id") or t.get("id")) not in SKIP:
            tasks.append(t)
if LIMIT:
    tasks = tasks[:LIMIT]
emit({"event": "config", "models": MODELS, "langs": LANGS, "difficulty": DIFFICULTY,
      "tasks": [t.get("instance_id") or t.get("id") for t in tasks],
      "rag": RAG, "max_repair": MAX_REPAIR})

results = []
for model in MODELS:
    provider = create_provider(model=model)
    safe = model.replace(":", "_").replace("/", "_")
    for task in tasks:
        tid = task.get("instance_id") or task.get("id")
        bench = ProgramBench(tasks_dir=TASKS, run_dir=str(OUT / safe), programbench_cli=PB_CLI)
        ws = OUT / safe / tid / "ws"
        try:
            res = bench.rebuild_instance(
                task, provider=provider, workspace=ws, max_repair=MAX_REPAIR,
                doc_provider=docs,
                on_attempt=lambda a, _m=model, _t=tid: emit(
                    {"event": "attempt", "model": _m, "task": _t, "i": a.index,
                     "resolved": a.resolved, "summary": a.summary}),
            )
            best = res.best_summary or {}
            rec = {"event": "task", "model": model, "task": tid,
                   "resolved": res.resolved, "attempts": len(res.attempts),
                   "passed": best.get("passed"), "total": best.get("total"),
                   "error_code": best.get("error_code")}
        except Exception as e:  # noqa: BLE001
            rec = {"event": "task", "model": model, "task": tid,
                   "error": f"{type(e).__name__}: {e}"}
        results.append(rec)
        emit(rec)

emit({"event": "SUMMARY"})
for model in MODELS:
    rows = [r for r in results if r.get("model") == model and r.get("event") == "task"]
    resolved = sum(1 for r in rows if r.get("resolved"))
    compiled = sum(1 for r in rows if r.get("error_code") not in ("compile_failed", None) or (r.get("passed") or 0) > 0)
    emit({"event": "model-summary", "model": model, "tasks": len(rows),
          "resolved": resolved, "any_tests_passed": compiled})
logf.close()
