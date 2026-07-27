"""ProgramBench 10x2 sweep: glm-5.2 + qwen3-coder-next via the Ollama-Cloud bridge.

Uses the fixed swe-agent preset. Writes per-task JSONL (flushed) + a summary.
Scratch harness — safe to delete; runs land in pb-runs/ (gitignored).
"""
import json
import os
from pathlib import Path

# --- env: load .env, configure the Claude-Code-style bridge + live gate ---
ROOT = Path(__file__).resolve().parent
for line in (ROOT / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
os.environ["ANTHROPIC_BASE_URL"] = "http://localhost:11434"
os.environ["ANTHROPIC_API_KEY"] = os.environ.get("OLLAMA_API_KEY", "")
os.environ["ANTHROPIC_AUTH_TOKEN"] = os.environ.get("OLLAMA_API_KEY", "")
os.environ["CHIMERA_PROGRAMBENCH_LIVE"] = "1"  # arm64 host: force-run under QEMU

from chimera.agents.config import AgentConfig  # noqa: E402
from chimera.eval.benchmarks.programbench import ProgramBench  # noqa: E402
from chimera.providers.anthropic import AnthropicProvider  # noqa: E402


class BigBudgetAnthropic(AnthropicProvider):
    """Floor per-turn max_tokens at 8192 so reasoning models (glm-5.2) don't
    truncate mid-thought and emit empty turns. Covers complete() AND stream()
    since both route through _prepare_request."""

    def _prepare_request(self, messages, tools=None, temperature=0.0, max_tokens=None, thinking=None):
        return super()._prepare_request(messages, tools, temperature, max_tokens or 8192, thinking=thinking)


TASKS_DIR = "/Users/yadkonrad/dev_dev/year26/may26/ProgramBench/src/programbench/data/tasks"
PB_CLI = ("/Users/yadkonrad/dev_dev/year26/may26/ProgramBench/.venv/bin/programbench", "eval")
RUN_ROOT = ROOT / "pb-runs" / "2026-06-17-sweep"
MODELS = ["glm-5.2:cloud", "qwen3-coder-next:cloud"]
LIMIT = 10

SWE = AgentConfig.from_markdown(str(ROOT / "chimera/agents/presets/swe-agent.md"))
RUN_ROOT.mkdir(parents=True, exist_ok=True)
logf = open(RUN_ROOT / "sweep.jsonl", "a")  # noqa: SIM115


def emit(rec):
    line = json.dumps(rec)
    print(line, flush=True)
    logf.write(line + "\n")
    logf.flush()


results = []
for model in MODELS:
    safe = model.replace(":", "_").replace("/", "_")
    run_dir = RUN_ROOT / safe
    run_dir.mkdir(parents=True, exist_ok=True)
    bench = ProgramBench(tasks_dir=TASKS_DIR, limit=LIMIT, run_dir=str(run_dir), programbench_cli=PB_CLI)

    def make_agent(instance, ws, _m=model):
        return SWE.build(BigBudgetAnthropic(model=_m))

    for task in bench.tasks():
        tid = task.get("instance_id") or task.get("id") or "unknown"
        ws = run_dir / tid / "ws"
        ws.mkdir(parents=True, exist_ok=True)
        try:
            res = bench.run_instance(task, workspace=ws, agent_factory=make_agent)
            wrote = sorted(p.name for p in ws.iterdir() if p.name not in ("_inputs", "submission.tar.gz"))
            try:
                passed = bench.evaluate(task, str(res.submission_tar))
            except Exception as e:  # noqa: BLE001
                passed = f"grade-error:{type(e).__name__}:{e}"
            rec = {"model": model, "task": tid, "steps": res.steps, "cost": round(res.cost or 0, 4),
                   "wrote": wrote, "n_files": len(wrote), "passed": passed, "error": res.error}
        except Exception as e:  # noqa: BLE001
            rec = {"model": model, "task": tid, "error": f"{type(e).__name__}: {e}"}
        results.append(rec)
        emit(rec)

# --- summary ---
(RUN_ROOT / "results.json").write_text(json.dumps(results, indent=2))
emit({"event": "SUMMARY"})
for model in MODELS:
    rows = [r for r in results if r["model"] == model]
    wrote = sum(1 for r in rows if r.get("n_files"))
    passed = sum(1 for r in rows if r.get("passed") is True)
    cost = sum((r.get("cost") or 0) for r in rows)
    emit({"event": "model-summary", "model": model, "tasks": len(rows),
          "wrote": wrote, "passed": passed, "cost_usd": round(cost, 4)})
logf.close()
