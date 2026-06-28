"""ProgramBench one-shot codegen + compile-repair loop.

Generate the whole source tree (incl. the required compile.sh -> ./executable
contract), grade it, feed the compile/grade errors back, regenerate, repeat.
Scratch harness — gitignored. Env: PB_INSTANCE, PB_LANG, PB_MAX_REPAIR.
"""
import json
import os
import re
import shutil
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

from chimera.eval.benchmarks.programbench import (  # noqa: E402
    ProgramBench, package_submission, parse_eval_json,
)
from chimera.providers.anthropic import AnthropicProvider  # noqa: E402
from chimera.providers.base import Message  # noqa: E402

TASKS = "/Users/yadkonrad/dev_dev/year26/may26/ProgramBench/src/programbench/data/tasks"
PB_CLI = ("/Users/yadkonrad/dev_dev/year26/may26/ProgramBench/.venv/bin/programbench", "eval")
INSTANCE = os.environ.get("PB_INSTANCE", "abishekvashok__cmatrix.5c082c6")
LANG = os.environ.get("PB_LANG", "c")
MAX_REPAIR = int(os.environ.get("PB_MAX_REPAIR", "4"))
MODEL = "qwen3-coder-next:cloud"
PROJECT = INSTANCE.split("__")[-1].rsplit(".", 1)[0]
SRC_INPUTS = Path(f"pb-runs/2026-06-17-sweep/glm-5.2_cloud/{INSTANCE}/ws/_inputs")
WS = Path(f"pb-runs/_repair/{INSTANCE}/ws")
RUN_DIR = Path("pb-runs/_repair/run")


class BB(AnthropicProvider):
    def _prepare_request(self, messages, tools=None, temperature=0.0, max_tokens=None, thinking=None):
        return super()._prepare_request(messages, tools, temperature, max_tokens or 16384, thinking=thinking)


prov = BB(model=MODEL)

# --- spec ---
spec_parts = []
for f in sorted(SRC_INPUTS.rglob("*")):
    if not f.is_file() or ".git" in f.parts or f.name == "executable":
        continue
    if f.suffix.lower() in (".png", ".gif", ".jpg", ".jpeg", ".ico", ".bmp", ".o", ".a"):
        continue
    try:
        txt = f.read_text(errors="replace")
    except Exception:
        continue
    spec_parts.append(f"=== {f.relative_to(SRC_INPUTS)} ===\n{txt[:16000]}")
spec = "\n\n".join(spec_parts)

CONTRACT = """CRITICAL submission contract — the grader runs `chmod +x ./compile.sh && ./compile.sh`,
then invokes `./executable` with CLI args and compares output to the original program:
  - Ship `compile.sh` at the ROOT. It builds your source and places the runnable
    program at `./executable` (that EXACT name), then `chmod +x executable`.
  - Example compile.sh for C:
      #!/bin/bash
      set -e
      gcc -O2 -o executable src/*.c -lncurses   # or: make && cp <binary> executable
      chmod +x executable
  - `./executable` must accept the same CLI flags and reproduce the documented behavior.
  - The cleanroom has the language toolchain and CAN fetch dependencies (cargo/go
    resolve crates/modules normally) — use the same libraries the original uses."""

FORMAT = """Output ONLY the files — NO explanation, NO prose, NO commentary before or after,
NO markdown fences. Each file delimited EXACTLY like this:
>>>> FILE: <relative/path>
<full verbatim file content>
>>>> ENDFILE"""


def gen_prompt(prior, errors):
    if prior is None:
        return (f"Rebuild the program **{PROJECT}** (language: {LANG}) from scratch using its "
                f"documentation as the spec.\n\nSPEC:\n{spec}\n\n{CONTRACT}\n\n{FORMAT}\n\n"
                f"Include compile.sh + every build/source file needed. Begin now.")
    dump = "\n\n".join(f">>>> FILE: {p}\n{c}\n>>>> ENDFILE" for p, c in prior.items())
    return (f"Your previous rebuild of **{PROJECT}** ({LANG}) FAILED when the grader built/ran it.\n\n"
            f"{CONTRACT}\n\nCURRENT FILES:\n{dump}\n\nGRADER ERROR OUTPUT:\n{errors}\n\n"
            f"Diagnose and FIX it (common causes: compile.sh missing or not producing ./executable; "
            f"wrong build command; missing source; link errors; dependency/version conflicts; actual "
            f"Rust/C compile errors in the source). Re-output the FULL content of every file you change "
            f"(always include the file(s) named in the error); files you omit are kept unchanged."
            f"\n\n{FORMAT}\n\nBegin now.")


def parse_files(out):
    files = {}
    for m in re.finditer(r">>>>\s*FILE:\s*(.+?)\r?\n(.*?)>>>>\s*ENDFILE", out, re.DOTALL):
        path = m.group(1).strip().strip("`").strip()
        content = m.group(2)
        if content.startswith("```"):
            content = re.sub(r"^```[^\n]*\n", "", content)
            content = re.sub(r"\n```\s*$", "\n", content)
        files[path] = content
    return files


def _focus(text, limit=3000):
    """Keep error-looking lines + the tail (compilers print errors at the end),
    not the head (which for cargo is just dependency-download spam)."""
    lines = text.splitlines()
    errly = [ln for ln in lines if re.search(
        r"error|cannot find|expected|undefined|not found|failed|unresolved|mismatch|no method|trait",
        ln, re.I)]
    body = ("KEY LINES:\n" + "\n".join(errly[-50:]) + "\n\n") if errly else ""
    return (body + "TAIL:\n" + "\n".join(lines[-50:]))[-limit:]


def extract_errors(ejp):
    d = json.load(open(ejp))
    parts = []
    for x in d.get("log", []):
        if x.get("step") in ("compile", "run") and x.get("output"):
            parts.append(f"{x['step']} output:\n{_focus(str(x['output']))}")
    if not parts and d.get("error_details"):
        parts.append(_focus(str(d["error_details"])))
    if d.get("error_code") is None:  # compiled — surface a few failing tests
        fails = [t.get("name", "") for t in d.get("test_results", []) if not t.get("passed", True)][:8]
        if fails:
            parts.append("sample failing tests:\n" + "\n".join(fails))
    return ("\n\n".join(parts))[-4000:] or "(no detail in eval.json)"


bench = ProgramBench(tasks_dir=TASKS, run_dir=str(RUN_DIR), programbench_cli=PB_CLI)
task = next(t for t in bench.tasks() if (t.get("instance_id") or t.get("id")) == INSTANCE)

files, errors, best = None, None, None
for attempt in range(MAX_REPAIR + 1):
    out = prov.complete([Message.user(gen_prompt(files, errors))], max_tokens=16384).content
    new_files = parse_files(out)
    if not new_files:
        # Model replied with prose instead of file blocks — nudge and retry
        # (keep prior files via merge so we don't lose the tree).
        print(f"[attempt {attempt}] no file blocks; nudging for format. head: {out[:120]!r}", flush=True)
        errors = ("Your last reply had NO files in the required format — only prose. "
                  "Re-output the fix as >>>> FILE: ... >>>> ENDFILE blocks ONLY, no prose.\n\n"
                  + (errors or ""))
        continue
    # Merge: repair rounds update/add files; omitted files are KEPT (the model
    # often re-emits only what it changed). Prevents regressing to a 1-file tree.
    files = new_files if files is None else {**files, **new_files}
    if WS.exists():
        shutil.rmtree(WS)
    WS.mkdir(parents=True)
    for p, c in files.items():
        fp = WS / p
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(c)
    tar = WS / "submission.tar.gz"
    package_submission(WS, tar)
    try:
        passed = bench.evaluate(task, str(tar))
    except Exception as e:  # noqa: BLE001
        print(f"[attempt {attempt}] EVALUATE RAISED: {type(e).__name__}: {e}", flush=True)
        passed = False
    ejp = RUN_DIR / INSTANCE / f"{INSTANCE}.eval.json"
    summ = parse_eval_json(ejp) if ejp.exists() else {}
    print(f"[attempt {attempt}] files={list(files.keys())} -> resolved={passed} | "
          f"{summ.get('passed')}/{summ.get('total')} err={summ.get('error_code')}", flush=True)
    if ejp.exists():
        shutil.copy2(ejp, ejp.parent / f"attempt{attempt}.eval.json")
    if best is None or (summ.get("passed", 0) or 0) > (best.get("passed", 0) or 0):
        best = summ
    if passed:
        print("=== RESOLVED ===", flush=True)
        break
    errors = extract_errors(ejp) if ejp.exists() else "(no eval.json)"
    if attempt == MAX_REPAIR:
        print("=== max repairs reached ===", flush=True)

print(f"BEST: {best}", flush=True)
