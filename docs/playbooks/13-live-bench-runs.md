# Playbook 13 — Live bench runs (the anti-improvisation rules)

Every rule here was paid for with a real failure. Follow the sequence; do not
freestyle live runs.

## The invariants (violating any of these produced garbage before)

1. **Concurrency ≤ 4 against one model account.** Modal can fan out to
   hundreds of containers; the model account cannot serve them. The 39-cell
   uncapped grid collapsed into 36 errors. The cap lives in
   `scripts/modal_bench_app.py::_MAX_CONCURRENCY` — raise it only with proof
   the account's rate limit allows more.
2. **Smoke before grid.** One cell (`--limit 2`, one agent, one bench) must
   come back clean before any multi-cell run. A smoke costs ~$0.01; a garbage
   grid costs an hour and real money.
3. **Per-column outputs, resume-safe.** One output file per agent/column, skip
   files that exist. The 75-cell single-call depth run timed out at 90min and
   wrote *nothing*.
4. **Size timeouts from measured per-task time, not hope.** Bare loops run
   2–4 min/task on codegen; the flagship ~45s/task. n=25 for a loop needs
   >90 min. Measure one task, multiply, add 50%.
5. **Integrity-scan before publishing.** `uv run python scripts/verify_status.py`
   must be green — specifically `data-integrity` (no `status=error` cell with
   `passed>0` in the latest grid) — before any number goes into a doc.
   A uniform-100% column is a harness-bug signature, not a result
   (fence-extraction 2026-07-06; HumanEval+ `check()` never invoked 2026-07-08).
6. **Numbers come from files, not memory.** Every published table cites its
   `data/*.json`. If the file doesn't exist, the number doesn't exist.

7. **Hours-long runs MUST be detached + server-persisted.** A plain
   `modal run` keeps cells alive only while the local client is connected — a
   sleeping laptop terminated a 2h full-dataset run ("local client
   disconnected", all cells lost). Use `modal run --detach
   ...::grid_detached --run-id <id>`; cells persist to the
   `chimera-bench-results` Volume; fetch with `::collect --run-id <id>`.


## Before you blame Modal: the Python 3.12.8 trap

Before blaming infrastructure: the venv's CPython **3.12.8** fails every
`asyncio` TLS connect instantly (`OSError: [Errno 9] Connect call failed`),
so the Modal client retries and reports *"Could not connect to the Modal
server."* Verified 2026-07-24: status.modal.com fully green, `curl`/`nc`/
system CPython 3.12.9 all reached the same IP:443, only the 3.12.8 venv
failed. Cells already on the Volume were fine the whole time.

Diagnostic order, cheapest first:

1. <https://status.modal.com/> — is anything actually red?
2. `curl -s -o /dev/null -w '%{http_code}' https://api.modal.com` → 200 means reachable.
3. Retry pinned: `uv run --python 3.13 --extra modal-sandbox --extra anthropic modal volume ls chimera-bench-results <run-id>/`

Never re-fire a paid run on the assumption that the previous one died to an
outage until step 3 fails too — a detached grid's cells persist server-side
and are collectible later.

## The sequence for any live run

```
uv run python scripts/verify_status.py          # 1. all green first
set -a; source .env; set +a                     # 2. creds (never auto-loaded)
# 3. smoke: one cell
modal run scripts/modal_bench_app.py --agent react --bench mbpp --limit 2
# 4. the real run (throttled grid / per-column)
modal run scripts/modal_bench_app.py::grid --agents ... --benches ... --limit N
# 5. integrity scan on the saved file, then publish
uv run python scripts/verify_status.py
```

## Definition of Done (applies to ALL work, not just runs)

A task is **done** only when all four exist in the same push:
commit + test proving the behavior + a `verify_status.py` check (or cited
data file) + the tracker box ticked. "Starting X" in a conversation is
**not** a state; if there is no commit, it did not happen.
