---
title: "Run an experiment, keep the evidence"
description: "A stdlib toolkit for benchmark sweeps and one-off runs: a stamped run directory with git provenance, a flushed JSONL ledger that survives a crash, resume-by-key, and a result.json shaped like a bench receipt. Plus chimera experiments list / show."
---

Every experiment worth running produces two things: a number, and the question
*what exactly produced it*. Chimera used to help with neither. Sweeps were
driven by one-off scripts that each invented a run directory, a progress file,
resume logic, and `.env` loading — five of them, five slightly different
answers, and one 336 MB directory of run output nobody noticed for a month.

`chimera.experiments` is the API those scripts needed. It is small, stdlib
only, and library-first: it does not run your experiment, it keeps the
evidence while you do.

```python
from chimera.experiments import start

run = start("pb-sweep", config={"model": "glm-5.2", "limit": 10})

done = run.seen("progress.jsonl", key="task")
for task in tasks:
    if task.id in done:
        continue                       # resume-by-default
    ok, cost = evaluate(task)
    run.jsonl("progress.jsonl", {"task": task.id, "ok": ok, "cost": cost})

run.finish({"passed": n, "total": len(tasks), "cost_usd": total_cost})
```

That is the whole surface for the common case. Everything below is detail.

## Where a run lives

```
~/.chimera/experiment-runs/pb-sweep/2026-07-27T14-03-11/
├── manifest.json      # written by start()
├── progress.jsonl     # your ledger, one flushed row per unit of work
├── result.json        # written by finish()
└── ws/…               # whatever else you write, via run.path()/run.subdir()
```

The root comes from the [path registry](./storage-and-paths), so `$CHIMERA_HOME`
or `[storage] root` relocates experiment runs along with everything else, and
`chimera gc` — shipping next — will reclaim them under a
`[storage.experiment-runs]` retention rule. There is no second retention
mechanism in the toolkit, deliberately: until `gc` lands, runs keep forever,
and deleting one is `rm` on a directory you can see.

**A run cannot write outside its own directory.** Run names are validated as
single path components, and every path goes through `run.path()`, which refuses
absolutes, `..` traversal, and symlinks that lead out. This is what makes
reclaiming experiment output safe to automate and what keeps run output from
landing beside your code again.

## The manifest: which code produced this number

`start()` writes `manifest.json` before your first line of work runs:

```json
{
  "name": "pb-sweep",
  "stamp": "2026-07-27T14-03-11",
  "status": "running",
  "started_at": "2026-07-27T14:03:11Z",
  "config": {"model": "glm-5.2", "limit": 10},
  "argv": ["scripts/experiments/example_toolkit_run.py"],
  "cwd": "/Users/you/dev/chimera",
  "git": {"sha": "82af69d4…", "branch": "integrate/m-track", "dirty": true},
  "host": "your-machine.local",
  "pid": 21171,
  "chimera_version": "0.9.2.2.dev0"
}
```

`git.dirty` is the field that earns its place. A SHA alone says *which commit*;
`dirty: true` says **this result is not reproducible from that commit** — there
were uncommitted changes in the tree. That is the provenance half of the
receipts discipline in `docs/playbooks/13-live-bench-runs.md`.

Outside a git checkout, `git` is `{"sha": null, "branch": null, "dirty": null}`.
Recording provenance never blocks a run.

## The ledger: `jsonl` and `seen`

```python
run.jsonl("progress.jsonl", {"task": "t0", "ok": True, "cost": 0.01})
```

One JSON object per line, appended and **flushed to the OS before the call
returns**. A run killed on the next line keeps this row. That is the point of
the ledger: it is not a log, it is the record of what has already been done.

```python
done = run.seen("progress.jsonl", key="task")   # {"t0", "t1", …}
```

`seen()` reads the ledger back as a set of keys. A hard kill can leave a
half-written final line; `seen()` and `rows()` skip anything unparseable rather
than raising, so the crash cannot poison the resume.

`run.rows("progress.jsonl")` returns the full records when you need to
aggregate at the end.

## Interrupted runs and `resume()`

A run that never calls `finish()` keeps `status: "running"`. Combined with the
recorded host and PID, that distinguishes three states:

| Manifest | Reality | `chimera experiments list` |
|---|---|---|
| `running`, PID alive on this host | still going | `running` |
| `running`, PID gone | crashed or killed | **`interrupted`** |
| `completed` | `finish()` was called | `completed` |
| `failed` | `fail()` was called, or the `with` body raised | `failed` |

```python
run = resume("pb-sweep")          # newest interrupted run for this name
run = resume("pb-sweep", "2026-07-27T14-03-11")   # a specific one
```

`resume()` refuses to reopen a *completed* run without an explicit stamp —
silently appending rows to a published result is how a receipt gets corrupted.

For the common shape, ask `start()` to do it in one call:

```python
run = start("pb-sweep", config=cfg, resume=True)
```

Reattach to the newest interrupted run if there is one, otherwise begin a new
one. The loop body is identical either way, which is the whole ergonomic point:
you write the resumable version once and it works on the first run too.

## Free-form artifacts

```python
ws = run.subdir(f"ws/{task_id}")          # created, inside the run
(ws / "solution.py").write_text(src)

run.write_text("notes.md", "…")
run.write_json("params.json", {"seed": 7})
run.path("logs/raw.txt").write_text(out)  # parent dirs created for you
```

`run.dir` is the directory itself if you need to hand it to something else.

## Finishing: `result.json` as a bench receipt

```python
run.finish({"passed": 8, "total": 10, "cost_usd": 1.25, "benchmark": "mbpp"})
```

writes:

```json
{
  "run_id": "pb-sweep/2026-07-27T14-03-11",
  "name": "pb-sweep",
  "stamp": "2026-07-27T14-03-11",
  "model": "glm-5.2",
  "started_at": "2026-07-27T14:03:11Z",
  "ended_at": "2026-07-27T14:41:02Z",
  "git": {"sha": "82af69d4…", "branch": "integrate/m-track", "dirty": true},
  "config": {"model": "glm-5.2", "limit": 10},
  "cells": [
    {
      "agent_id": "pb-sweep",
      "benchmark": "mbpp",
      "total": 10,
      "passed": 8,
      "pass_rate": 0.8,
      "cost_usd": 1.25,
      "status": "completed"
    }
  ]
}
```

That `cells` shape is the one `data/*.json` receipts use and
`scripts/render_observatory.py` reads. For a matrix run, pass the list
yourself:

```python
run.finish({"cells": [
    {"agent_id": "react", "benchmark": "mbpp", "passed": 5, "total": 5, "cost_usd": 0.4},
    {"agent_id": "react", "benchmark": "humaneval", "passed": 3, "total": 5, "cost_usd": 0.6},
]})
```

### finish() refuses a receipt the observatory would reject

Before writing anything, `finish()` checks the same invariants the observatory
enforces at publish time:

- `passed` may not exceed `total`;
- a supplied `pass_rate` must agree with `passed / total`;
- a `status_counts` tally must sum to `total`;
- `status: "error"` may not accompany passes.

A violation raises `ValueError`, writes nothing, and leaves the run open so you
can correct and call again. The reason to check here rather than only at
publish time is that *here* is where someone can still explain the number.

A clean-status `0/n` is **recorded**, not refused — the run really produced it,
and the ledger is what you diagnose from. It is the *renderer* that refuses to
publish it, because a uniform zero is the harness-gap signature, not a score.

### Promoting a result into `data/`

Copying is a deliberate human act. Nothing in the toolkit writes to `data/`,
and nothing should: `data/` is the curated receipt set that backs published
numbers.

```bash
cp ~/.chimera/experiment-runs/pb-sweep/2026-07-27T14-03-11/result.json \
   data/modal-grid-observatory-pb-sweep.json
```

The rename matters: the observatory scans a fixed pattern list, so a receipt
filed under a name it does not glob is simply never read.

## The CLI

```
chimera experiments list [<name>] [--json]
chimera experiments show <name>[/<stamp>] [--json]
```

```
$ chimera experiments list
2 run(s) under /Users/you/.chimera/experiment-runs

  example-sweep/2026-07-28T04-46-14  completed        3.5KB  7/10 (70.0%)  $0.0000
  pb-sweep/2026-07-28T05-02-33       interrupted      1.4KB
```

`show` prints the manifest (including the git SHA and dirty flag), the files
the run produced, and the receipt. A bare name shows the newest run.

Reclaiming old runs is **not** here. `experiment-runs` is a registry store, so
retention belongs to `chimera gc` (shipping next) with its dry-run-first rules;
a second pruning path is exactly the drift this subsystem exists to end.

## A worked example you can run

`scripts/experiments/example_toolkit_run.py` is `pb_sweep.py` rewritten on the
toolkit, with the model calls replaced by two offline "solvers" so it runs
anywhere — no credentials, no network, no Docker.

```bash
uv run python scripts/experiments/example_toolkit_run.py
chimera experiments show example-sweep
```

To watch the crash story end to end:

```bash
uv run python scripts/experiments/example_toolkit_run.py --crash-after 4
chimera experiments list            # -> interrupted
uv run python scripts/experiments/example_toolkit_run.py
#   resuming example-sweep/…, 4 unit(s) already done
```

The first invocation aborts with `os._exit()` — no cleanup, no flush, a real
crash. The ledger keeps all four rows anyway, and the second invocation runs
only the remaining six.

## Loading a `.env`

The pb drivers each hand-parsed `.env`. Don't:

```python
from chimera.config.dotenv import load_dotenv

load_dotenv(".env")          # existing environment wins
```

## Reference

| Call | What it does |
|---|---|
| `start(name, config=…, resume=False, stamp=None)` | Open a run; write `manifest.json` |
| `resume(name, stamp=None)` | Reattach to an interrupted (or named) run |
| `run.dir` | The run directory |
| `run.path(rel)` | A contained path; parent dirs created |
| `run.subdir(rel)` | A contained directory, created |
| `run.jsonl(file, record)` | Append one flushed JSON line |
| `run.seen(file, key="id")` | Set of keys already recorded |
| `run.rows(file)` | All parseable records |
| `run.write_text(file, …)` / `run.write_json(file, …)` | Replace a file inside the run |
| `run.finish(summary)` | Write `result.json`; status → `completed` |
| `run.fail(reason)` | Status → `failed`, with the reason |
| `list_runs(name=None)` / `load_run(ref)` | What the CLI reports |

`Run` is also a context manager: a body that raises records `failed` with the
exception text, and a body that returns without finishing leaves the run
`running` — an interrupted run, resumable, which is the honest record of what
happened.
