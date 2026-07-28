# Storage, state mapping, and the experiment toolkit

**Status: 🔵 Design — nothing in this document is built.** It is the plan that
turns a week of reactive cleanup into a subsystem. Milestones at the end;
tracker rows in `docs/specs/README.md`.

## Why this exists (receipts, not vibes)

An owner audit (2026-07-27) found the state story was accretion, not design:

- **1.3 GB of run output at the repo root** — `pb-runs/` (336 MB) from scratch
  drivers, `runs/` (944 MB) from the external Terminal-Bench harness — plus the
  drivers themselves as the root's only loose `.py` files, six of them
  gitignored *while tracked*.
- **A 2.0 GB checkpoint tree** (`.chimera_checkpoints/0`): a full tree copy
  including `.venv`, `site/node_modules` (759 MB) and a duplicate of `runs/`.
  Nothing surfaced it until a human got angry.
  > **Correction (M1, 2026-07-27):** this document originally said "its writer
  > was deleted months ago." **That was wrong.** The writer is *live* —
  > `LocalEnvironment.setup()` creates `<workdir>/.chimera_checkpoints` on
  > every setup and `CheckpointManager.create()` fills it with full tree
  > copies. Verified by running `setup()` and watching the directory appear.
  > This makes M3 *more* urgent, not less: the problem is an active unbounded
  > writer, not dead residue, so the tree regenerates.
- **Retention exists for exactly one store** (cohorts: `[tui.cohorts]`
  retain / max-age-days). Sessions, eventlog, history, checkpoints,
  experiment runs: fire-and-forget.
- **Three separate copies of the "not source" directory list** —
  `chimera/tools/list_files.py` (`_IGNORED_DIRS`), `chimera/tools/repo_map.py`,
  `chimera/tools/definition_lookup.py` — drifting independently, and the
  checkpoint machinery used none of them.
- **`chimera doctor` checks providers only.** No storage surface exists.
- **Experiments have no API.** The ProgramBench work was done with one-off
  root-level scripts, each hand-rolling run dirs, progress files, resume
  logic, and env loading — which is exactly how the rot was created.

Two stopgaps already landed (repo-root gate in `tests/test_repo_hygiene.py`;
drivers redirected to `~/.chimera/experiment-runs`). This spec is the actual
fix.

## Design principles

1. **One registry is the single truth.** A store that is not declared in the
   registry does not exist; anything on disk that isn't claimed by it is an
   *orphan* and gets reported. Orphans self-report — they never wait for an
   audit.
2. **Archive, never delete.** No tool in this design deletes data by default.
   `gc` is dry-run first, opt-in retention, and its destructive mode names
   every path and why. Datasets and `data/` receipts are *never* eligible.
3. **Retention is opt-in**, matching the cohort precedent: everything keeps
   forever until the owner writes a retention line in config.
4. **Zero-dependency core.** Everything here is stdlib.
5. **The paved road beats the guard rail.** Guards (root gate, static write
   gate) catch violations; the paths module and the experiment toolkit make
   the correct thing the *easy* thing, so violations stop being written.

---

## Part 1 — the path registry and `[storage]` config (M1)

New module `chimera/config/paths.py`:

```python
@dataclass(frozen=True)
class Store:
    name: str        # "cohorts"
    scope: str       # "user" (under chimera_home) | "project" (under <proj>/.chimera)
    rel: str         # relative path under its scope root
    writer: str      # owning module, for doctor's report
    prunable: bool   # eligible for [storage.<name>] retention at all
    note: str = ""

def chimera_home() -> Path: ...        # $CHIMERA_HOME > [storage] root > ~/.chimera
def project_state_dir(project: Path) -> Path: ...   # <project>/.chimera
def store_path(name: str, project: Path | None = None) -> Path: ...
def all_stores() -> tuple[Store, ...]: ...
```

Resolution precedence for the root: env `CHIMERA_HOME` → `[storage] root` in
the one config chain (`chimera/config/user_config.py`, T13) → `~/.chimera`.

### The mapping — current state → registry (the actual migration table)

| Store | Today (measured 2026-07-27) | Writer | Registry entry | Prunable |
|---|---|---|---|---|
| `datasets` | `~/.chimera/datasets` (144 MB); env `CHIMERA_DATASETS_DIR` kept | `chimera/eval/datasets.py` | `user:datasets` | **never** — deliberate artifacts |
| `cohorts` | `~/.chimera/cohorts` (39 MB); retention exists | `chimera/tui/cohort.py` | `user:cohorts` | yes (existing `[tui.cohorts]` read as legacy alias) |
| `sessions` | `~/.chimera/sessions` | `chimera/sessions/` | `user:sessions` | yes |
| `eventlog` | `~/.chimera/eventlog` (956 KB) | `chimera/sessions/eventlog/` | `user:eventlog` | yes |
| `history` | `~/.chimera/history` | REPL history (confirm writer in M1 sweep) | `user:history` | yes |
| `projects` | `~/.chimera/projects` | confirm in M1 sweep | `user:projects` | yes |
| `function_synthesis` | `~/.chimera/function_synthesis` | `chimera/function_synthesis/` | `user:function-synthesis` | **never** — model artifacts |
| `tasks` | `~/.chimera/tasks` (0 B) | confirm in M1 sweep | `user:tasks` | yes |
| `experiment-runs` | `~/.chimera/experiment-runs` (336 MB); env `CHIMERA_PB_RUNS` for the pb subtree | `scripts/experiments/*` today; the toolkit (Part 4) tomorrow | `user:experiment-runs` | yes |
| project state | `<proj>/.chimera` — `sessions/`, `todo.json` (39 MB here) | `chimera/cli/code.py`, todo tool | `project:state` | yes |
| checkpoints | *(orphan archived out)* future writes | `chimera/checkpoints.py` after M3 | `project:checkpoints` (under `<proj>/.chimera/checkpoints`) | yes, retain-N |

> **Reality check (M1):** the table above lists 11 stores; the sweep found
> **36** (28 user-scope, 8 project-scope; 19 never-prunable). The other 25 were
> not optional detail — each would have surfaced as a false orphan in M2.
> `history` is a **file**, not a directory, so entry-count retention cannot
> apply to it; project-scope `sessions` is written by
> `assembly/coding_agent.py`, not `cli/code.py` as claimed. See
> `chimera/config/paths.py` for the authoritative set.

M1 ends with a grep-audit: **zero** `Path.home() / ".chimera"` constructions
outside `paths.py`. Existing env vars (`CHIMERA_DATASETS_DIR`,
`CHIMERA_PB_RUNS`) keep working as per-store overrides.

`[storage]` config shape:

```toml
[storage]
root = "~/.chimera"          # optional; env CHIMERA_HOME wins

[storage.sessions]
retain = 200                  # keep newest N   (absent = keep forever)
max-age-days = 90             # and/or drop older than this

[storage.checkpoints]
retain = 5
```

## Part 2 — the surfaces: `doctor` storage section and `chimera gc` (M2)

**`chimera doctor`** grows a storage section (today it checks providers only):
one row per registry store — path, size, entry count, newest/oldest age,
retention config if any — for both scopes, plus an **orphans** subsection: any
directory under `chimera_home()` or `<proj>/.chimera` not claimed by the
registry, with its size.

> **Scope fix (M1, 2026-07-27):** as first written this scan covered
> `chimera_home()` and `<proj>/.chimera` — and would have **walked straight
> past** `<workdir>/.chimera_checkpoints`, which sits *beside* `<proj>/.chimera`
> rather than inside it. The scan must cover project-root `.chimera*` siblings,
> or it misses the exact 2 GB tree that motivated this spec. `--json` for scripting.

**`chimera gc`**: iterates registry stores that are `prunable` *and* have
retention configured. Dry-run is the default and prints every candidate with
the rule that selected it; `--apply` acts. It can only prune what the registry
names — there is structurally no code path to delete an unknown directory.
The existing cohort pruner becomes a caller of this engine (one retention
implementation, not two), keeping its guarantee: never the live cohort.

## Part 3 — checkpoint hardening and the one ignore list (M3)

- Extract the ignore set into **one module** (`chimera/config/ignore.py`,
  `NOT_SOURCE_DIRS`) and make `list_files`, `repo_map`,
  `definition_lookup`, and the checkpoint writer all consume it. Three
  hand-copied lists become one.
- Checkpoints write under `project:checkpoints` via the registry, **exclude
  `NOT_SOURCE_DIRS`**, warn at create time past a size threshold, and honor
  `retain` (default when enabled: last 5).
- Canary-style test: checkpointing a tree containing a fake `.venv` and
  `node_modules` must produce a checkpoint **without** them, and restore must
  round-trip the real files. (The 2 GB incident, as a permanent regression
  test.)

## Part 4 — the experiment toolkit: `chimera/experiments/` (M4)

The reason scratch scripts existed is that Chimera offered no API for "run an
experiment and keep the evidence." The toolkit is that API — small, stdlib,
library-first:

```python
from chimera.experiments import start, resume

run = start("pb-sweep", config={"model": "glm-5.2", "limit": 10})
# → ~/.chimera/experiment-runs/pb-sweep/2026-07-27T14-03-11/
#   manifest.json: name, stamp, config, argv, cwd, git {sha, dirty}, status=running

for task in tasks:
    if task.id in run.seen("progress.jsonl", key="task"):
        continue                       # resume-by-default, the pattern every
    ...                                # pb script hand-rolled
    run.jsonl("progress.jsonl", {"task": task.id, "ok": ok, "cost": cost})

run.finish({"passed": n, "total": t, "cost_usd": cost})
# → result.json, status=completed; a crash leaves status=running, which
#   doctor lists as "interrupted" and resume() picks up
```

- `run.dir` / `run.path("ws/…")` for free-form artifacts; everything lives
  under the registry's `experiment-runs` store — **a toolkit run cannot write
  outside it.**
- The manifest answers *"which code produced this number"* (git SHA + dirty
  flag) — the provenance half of the receipts discipline
  (`docs/playbooks/13-live-bench-runs.md`).
- `result.json` is shaped like a bench receipt cell so a curated result can be
  **copied** into `data/` deliberately. Never automatically — `data/` stays
  human-curated.
- CLI: `chimera experiments list` / `show <name>[/<stamp>]`; pruning goes
  through `gc`, not a second mechanism.
- **One ported exemplar** (`scripts/experiments/example_toolkit_run.py`)
  demonstrating the API against a real small run. The five historical pb
  scripts stay **frozen** — their value is provenance for June's numbers;
  their README already points new work at the toolkit.

## Part 5 — enforcement (M5, can land with M1)

- Extend `tests/test_repo_hygiene.py` with a static scan of `chimera/**/*.py`
  that fails on cwd-relative directory writes (`os.makedirs("lit…")`,
  `Path("lit…").mkdir`, `open("lit…/`, `shutil.copytree(…, "lit…")`), with a
  commented allowlist for any legitimate case it finds. The manual sweep that
  proved `chimera/` clean, made permanent.
- Already landed: the repo-root gate; the playbook rule that external
  harnesses are invoked from outside the repo (a code gate cannot reach them).

## Milestones

| M | Scope | Size | Depends on | Done means |
|---|---|---|---|---|
| M0 | Ship 0.9.2.1 (staged stack → tag → uvx verify → dev-bump both version sites) | S | owner go | PyPI verified from outside the repo |
| M1 | `paths.py` registry + `[storage]` + migrate all call sites + zero-stragglers audit | M | M0 | grep-audit clean; guide; tests |
| M2 | `doctor` storage + orphans; `chimera gc` (dry-run default; cohort pruner rides it) | M | M1 | orphan fixture test; gc never names an unregistered path |
| M3 | one ignore module; checkpoint exclusions/retain-N/size-warn + canary test | M | M1 | fake-venv canary green |
| M4 | `chimera/experiments/` toolkit + CLI list/show + one ported exemplar + guide | M-L | M1 | exemplar produces a manifest/result run end-to-end |
| M5 | static cwd-write gate | S | — | scan green on current tree; seeded violation goes red |

Every milestone carries the standing DoD: tests, `ruff`/`mypy`, CI-posture,
user guide, changelog entry landing **with** the work.

## Non-goals

- **No auto-pruning by default, ever.** Retention runs only where configured.
- **No deletion anywhere in this design.** `gc --apply` is the single
  destructive surface, opt-in, dry-run-first (owner rule 2026-07-27:
  archive/relocate, never delete-by-default).
- **No porting of the five frozen pb scripts** beyond the one exemplar.
- **No touching `data/` receipts or datasets** from any lifecycle tool.
- **No cloud/state sync.** Local machine only.
