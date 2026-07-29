---
title: Modal Bench Fan-Out — the whole grid, parallel, in the cloud
description: Spec + checkpoint for running N agents × M benchmarks as concurrent Modal functions. Written so the work survives a disconnect.
---

# Modal Bench Fan-Out

Run an agent × benchmark **grid** as **concurrent Modal functions** — the full
comparison in minutes, no serial timeouts. This is the fix for the local
depth-run saga (sequential single-account runs that kept timing out); Modal's
scheduler fans cells out across containers, each isolated.

## Status / checkpoint (update as we go)

- [x] **Slice 1** — `bench-matrix --env modal` (per-task Modal sandbox + `--modal-gpu`). Shipped `865227e`. Live: react×mbpp 100%, Tesla T4 confirmed.
- [x] **Slice 2** — `scripts/modal_bench_app.py`: one cell runs 100% on Modal (orchestration + inference via `chimera-glm` secret + execution). Shipped `5e4f55c`. Live: react×mbpp n=2 = 2/2.
- [x] **Slice 3 (THIS SPEC)** — parallel fan-out: `grid` entrypoint in `scripts/modal_bench_app.py` uses `fn.starmap(cells, return_exceptions=True)`; collects → agents×benches table + `data/modal-grid-<ts>.json`. Committed.
- [x] Proof run DONE: flagship + 3 loops × {mbpp, livecodebench} (8 cells) concurrent on Modal — 0 errors, $0.83, wall 611s vs 782s sum-of-cells. ~~Flagship 100%/100% (only agent to sweep both); tree-of-thought weakest.~~ **⊘ RETRACTED — do not cite:** half of "both" is the `livecodebench` column, retracted in `scripts/render_observatory.py`, so there is no sweep to be the only agent to make and no ranking left standing. The **infra** result is untouched — 8 cells, 0 errors, $0.83, 611s vs 782s sum-of-cells measure the fan-out, not the grader. Same withdrawal as `docs/benchmarks/modal-cloud-benches.md`. Data: `data/modal-grid-20260707-201224.json`; write-up: `docs/benchmarks/modal-cloud-benches.md`.
- [ ] (later) `chimera bench-modal` first-class CLI; GPU model-serving via `ModalProvider`.

## What already exists (don't rebuild)

- `scripts/modal_bench_app.py` — `app = modal.App("chimera-bench")`, image bakes in
  local `chimera/` (PYTHONPATH=/pkg) + `~/.chimera/datasets` (HOME=/root); secret
  `chimera-glm` (ANTHROPIC_API_KEY/BASE_URL/MODEL). Functions `run_cell_cpu` /
  `run_cell_gpu(gpu="T4")` both call `_run_one(agent, bench, limit, model,
  max_tool_calls, max_cost) -> report_dict`. Entrypoint `main(...)` runs ONE cell.
- Modal auth: `~/.modal.toml`, workspace `0bserver07`. `modal 1.5.0` installed.
- Runnable benches (staged): mbpp, humaneval-plus, mbpp-plus, livecodebench, math500, tau-bench, swe-bench.
- 13 agents: react, plan-execute, reflexion, tree-of-thought, coding-agent (flagship), full-tools, action-first, minimal, explore, swebench, retry-min, lint-loop, plan-act.

## Design

Add a `grid` local entrypoint to `scripts/modal_bench_app.py`:

1. Parse `--agents a,b,c` and `--benches x,y` (comma lists) + `--limit`, `--model`,
   `--gpu`, budget flags.
2. Build the cell arg-list: `cells = [(a, b, limit, model, mtc, mc) for a in agents for b in benches]`.
3. Fan out with Modal's parallel map: `results = list(fn.starmap(cells))` where
   `fn = run_cell_gpu if gpu else run_cell_cpu`. `.starmap` runs every cell
   concurrently across Modal containers and returns results in input order.
   (Each `_run_one` already returns a JSON-safe report dict for its single cell.)
4. Flatten each report's `cells[0]` into a combined grid; print an agents×benches
   pass-rate table; write `data/modal-grid-<ts>.json` (timestamp passed in from
   the local side — Modal fns can't call time; or stamp after collection locally).
5. Be resilient: a cell that raises inside Modal should surface as an `error`
   cell, not sink the whole grid. `.starmap` with `return_exceptions=True` (Modal
   supports it) OR wrap `_run_one` in a try/except that returns an error dict.

### Modal API notes
- `Function.starmap(iterable_of_tuples, return_exceptions=True)` — parallel, ordered.
- Concurrency is governed by Modal's autoscaler (many containers); no local serial bottleneck.
- Reuse the SAME image + secret already defined; only add the entrypoint (+ maybe a
  small `_safe_run_one` wrapper for per-cell error isolation).

## Acceptance

- `modal run scripts/modal_bench_app.py::grid --agents coding-agent,react,reflexion,tree-of-thought --benches mbpp,livecodebench --limit 5`
  runs **8 cells concurrently on Modal**, prints a 4×2 pass-rate table, writes a JSON.
- Wall-clock ≈ the slowest single cell, NOT the sum (proves parallelism).
- A deliberately-bad agent/bench name yields an `error` cell without sinking the grid.

## Run commands

```bash
# the proof grid (flagship + 3 loops × 2 benches, concurrent):
modal run scripts/modal_bench_app.py::grid \
  --agents coding-agent,react,reflexion,tree-of-thought \
  --benches mbpp,livecodebench --limit 5 --model "glm-5.2[1m]"

# single cell (slice 2, already works):
modal run scripts/modal_bench_app.py --agent react --bench mbpp --limit 2
```

## Gotchas (learned live)
- Modal fns can't call `time`/`random` for filenames — stamp timestamps on the LOCAL side after collection.
- `_run_one` uses env=local INSIDE the Modal container (the container IS the sandbox); do NOT nest `--env modal` (Modal-in-Modal).
- Secret `chimera-glm` supplies ANTHROPIC_*; `create_provider(model="glm-5.2[1m]")` picks up BASE_URL from it.
- Datasets baked at `/root/.chimera/datasets` (HOME=/root) — `human-eval` (base) has no dataset; use `humaneval-plus`.
