# Experiment drivers

## New work starts here: `chimera.experiments`

**Do not copy a driver below as a starting point.** Everything they hand-rolled
— the run directory, the progress file, the resume loop, the summary — is now
an API:

```python
from chimera.experiments import start

run = start("my-sweep", config={"model": "glm-5.2", "limit": 10}, resume=True)
done = run.seen("progress.jsonl", key="task")
for task in tasks:
    if task.id in done:
        continue
    run.jsonl("progress.jsonl", {"task": task.id, "ok": ok, "cost": cost})
run.finish({"passed": n, "total": t, "cost_usd": cost})
```

Runs land under `~/.chimera/experiment-runs/<name>/<stamp>/` with a
`manifest.json` recording the git SHA and dirty flag, a JSONL ledger that
survives a crash, and a `result.json` shaped like a bench receipt. Inspect them
with `chimera experiments list` / `show`; reclaiming them is `chimera gc`'s job
(shipping next), never a second mechanism in the toolkit.

- **Guide:** `docs/guides/experiments.md`
- **Runnable example:** `example_toolkit_run.py` in this directory — the
  `pb_sweep.py` shape with the model calls replaced by offline solvers, so it
  runs with no credentials. `--crash-after N` demonstrates the crash/resume
  path for real.

## The frozen drivers

One-off harnesses kept for provenance, not for reuse. They were written to get
a specific number on a specific day and are preserved so those numbers can be
traced back to the code that produced them — which is the whole point of the
receipts discipline in `docs/playbooks/13-live-bench-runs.md`.

**They stay frozen.** They are not ported to the toolkit and not maintained;
rewriting them would break the tie between June's numbers and the code that
produced them, which is the only reason they still exist.

**These are not part of the shipped package.** The wheel is
`packages = ["chimera"]`, so nothing here reaches PyPI. They are also not
covered by the test suite, the type gate, or the benchmark canary
(`docs/guides/benchmark-canary.md`) — treat any number one of them printed as
unverified unless a receipt in `data/` backs it.

| Script | What it drove |
|---|---|
| `pb_sweep.py` | ProgramBench 10×2 sweep — glm-5.2 + qwen3-coder-next |
| `pb_repair.py` | ProgramBench one-shot codegen + compile-repair loop |
| `pb_matrix.py` | ProgramBench comparative matrix (one-shot + compile-repair) |
| `pb_agentic.py` | ProgramBench agentic sweep through the real `chimera code` |
| `scratch_modal_grade.py` | Modal-backed grading for ProgramBench |
| `scratch_modal_smoke.py` | Modal sandbox create-from-registry smoke |
| `scratch_bridge_smoke.py` | Live smoke of the productionized `ProgramBench.rebuild_*` path |

## Why they live here

They sat at the **repo root** — the only seven root-level Python files —
while `.gitignore` simultaneously listed six of them. That was contradictory:
`.gitignore` only affects *untracked* files, so those rules did nothing and
anyone reading them would have wrongly concluded the files were not in git.
(The list was also incomplete — `pb_agentic.py` was tracked and unlisted.)

Moved here 2026-07-27, with the dead ignore rules deleted. Original intent to
keep them under version control is unchanged (`e18cf1b6`, *"preserve
ProgramBench/Modal scratch harness under git"*); only the location and the
misleading ignore rules changed.

## Where their state lives

**Never at the repo root.** All five ProgramBench drivers read and write under
`~/.chimera/experiment-runs/pb-runs` (override: `CHIMERA_PB_RUNS`) — including
the historical `2026-06-17-sweep` data several of them consume, which was
relocated there 2026-07-27. Before that they wrote a repo-root `pb-runs/`
(336 MB accumulated) and `pb_sweep.py` resolved `.env` relative to its own
location, both of which broke silently when the files moved — the redirect
fixed both. The repo root is gated (`tests/test_repo_hygiene.py`), and
Chimera-owned run state belongs under `~/.chimera` where a future storage
inventory can see it.

## If you add one

Reach for `chimera.experiments` first — that is what it is for, and a driver
built on it needs none of the plumbing this directory is full of. If you still
need a one-off: put it here rather than at the root, give it a docstring saying
what run it served and when, and remember it is unmaintained by construction —
a promotion to `scripts/` proper means tests and a type gate, not just a move.
`example_toolkit_run.py` is the exception: it *is* maintained, because it
documents the toolkit.
