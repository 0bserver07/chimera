# Modal amd64 ProgramBench Grading

**Status:** Proposal — unblocks the first live agentic ProgramBench numbers
(issue #160; cloud-sandbox track #144). Prior art already on disk in
`scripts/experiments/scratch_modal_grade.py`.

## Problem

ProgramBench grades a submission by building/running it inside an **amd64
cleanroom Docker image** (`compile.sh` → `./executable`, then the task's tests).
On arm64 (Apple Silicon) dev machines those images only run under **QEMU
emulation**, which is slow enough that grading is effectively unusable:

- The cleanroom images are **built locally, not reliably pullable**. Once the
  ones from a prior sweep are pruned, grading's `docker run <cleanroom> sleep
  2h` tries to pull a non-existent image and **hangs → 300 s TimeoutExpired**,
  and the QEMU load bogs the Docker daemon to unresponsive.
- Four separate blockers were peeled off reaching this diagnosis (documented in
  #160): image-pull hang → `_inputs` permission error → 1157-file `target/`
  submission bloat → grading pull-timeout.

The framework side is done and shipped (see
[coding-agent-harness-integration](coding-agent-harness-integration.md)):
`chimera eval --benchmark programbench --agent code` runs the real `chimera
code` CodingAgent, which **produces correct rebuilds** (verified: full Rust tree
for `zip-password-finder`, C for `figlet`). **Only grading is blocked, and only
by amd64 infrastructure** — not the framework, not the agent.

## What This Enables

- Live GLM-5.2`[1m]` (and any model) **agentic** ProgramBench pass-rates via the
  real coding agent — the actual goal of #160.
- **Native amd64 grading, no QEMU** — fast and **parallelizable** (fan grading
  out across tasks on Modal instead of serializing through one emulated daemon).
- The comparative matrix (bare `Agent` vs `CodingAgent`; model × model) on a
  genuinely agentic benchmark.

## Requirements

- Grade ProgramBench submissions on **native amd64 `modal.Sandbox`es**, matching
  the upstream `Evaluator`'s container semantics.
- **Reuse** the upstream grading logic (compile/run/test/parse) — swap only the
  container backend; do not reimplement grading.
- **Parallelize** across tasks (Modal concurrency), with per-task result
  persistence + resume (reuse the interruption-safe pattern already shipped).
- Keep the local-Docker grader working; Modal is **opt-in** (`--grader modal`).
- Resolve cleanroom-image reachability from Modal (registry vs local-only).

## Design Sketch

### `ModalContainerEnvironment` (exists — `scripts/experiments/scratch_modal_grade.py`)

A drop-in for upstream `programbench.container.ContainerEnvironment`, backed by a
`modal.Sandbox` (amd64, no QEMU). Implements exactly the methods the upstream
`Evaluator` calls: `execute`, `copy_in`, `copy_in_tar`, `commit` (→
`snapshot_filesystem()`), `cleanup`. Boots from a cleanroom image via
`modal.Image.from_registry(ref)`. Header notes it was *proven against figlet*.
Productionize verbatim into `chimera/env/modal_container.py` + tests.

### `ModalEvaluator` — the single injection point

```python
from programbench.eval.eval import Evaluator  # upstream

class ModalEvaluator(Evaluator):
    def __init__(self, *a, modal_app, cpus=4, **kw):
        super().__init__(*a, **kw)
        self._app, self._cpus = modal_app, cpus

    def _new_env(self, image: str):            # override the ONE docker hook
        return ModalContainerEnvironment(
            image=image, app=self._app, cwd="/workspace", cpus=self._cpus,
        )
```

Everything else in the upstream evaluator (staging the submission, running
`compile.sh`, invoking the tests, parsing to `eval.json`) is reused unchanged.

### Chimera grader hook

Add a `grader` selector to the ProgramBench adapter so `evaluate()` /
`run_instance` route through a `ModalEvaluator` when chosen:

```python
ProgramBench(..., grader="modal", modal_app=app)   # default "local"
```

Exposed on the CLI as `chimera eval --benchmark programbench --agent code
--grader modal`, and used by the `scripts/experiments/pb_agentic.py` sweep driver.

### Image availability (resolve first — see Open Questions)

`modal.Image.from_registry(ref)` requires the cleanroom image to be reachable.
Options, in order of preference: (a) it is already on a registry Modal can pull
(figlet suggests yes for some); (b) **push** the locally-built images to a
registry (GHCR/Docker Hub) once; (c) **build on Modal** from the task
Dockerfiles. A one-time `chimera pb-images push` helper may be warranted.

## File Layout

- `chimera/env/modal_container.py` — productionized `ModalContainerEnvironment`.
- `chimera/eval/benchmarks/programbench.py` — `grader`/`modal_app` params +
  `ModalEvaluator` wiring (or a sibling `programbench_modal.py`).
- `tests/eval/test_modal_container.py` — mocked-Sandbox unit tests + a
  `modal`-gated live test.
- Promote `scripts/experiments/pb_agentic.py` (currently scratch) into the repo as the reference
  sweep driver, or fold it into `chimera bench-compare`.

## Wiring

- `chimera eval --benchmark programbench --agent code --grader modal --resume
  --output data/programbench-glm-5.2-code-results.json`. ⊘ NO RECEIPT — that
  path is this proposal's *intended output*, not evidence: the run it describes
  has not happened, and the file is in no commit and on no disk. It is named
  here so the Acceptance Criteria below ("numbers saved to `data/`") stay
  checkable against something.
- Agent runs **local** (arm64 is fine — the agent is verified) → submission tar
  → **Modal-graded** amd64. (Running the agent on Modal too is a later option.)
- Modal auth surfaced through `chimera/env/factory.py` (`create_environment(
  "modal", ...)`) / project config.

## Acceptance Criteria

- `ModalContainerEnvironment` satisfies the upstream `ContainerEnvironment`
  contract (mocked unit tests + a live figlet boot).
- **Parity:** one task (figlet) graded on Modal yields the same score an
  (amd64) local Docker run would — no methodology drift from the emulated path.
- A **≥9-task agentic sweep completes on Modal**, results persisted + resumable,
  with GLM-5.2`[1m]` numbers saved to `data/` and a writeup (closes #11 → #12).

## Test Strategy

- **Unit:** each `ModalContainerEnvironment` method against a mocked
  `modal.Sandbox` (assert the right `exec`/tar/snapshot calls).
- **Live (modal-gated):** boot the figlet cleanroom on Modal, grade a known-good
  and a known-bad submission, assert pass/fail + score parity with local amd64.
- **Sweep:** 9-task run; kill mid-way and resume; assert no double-grading.

## Open Questions

1. **Registry reachability** — are the `programbench/…:task_cleanroom` images on
   a registry Modal can pull, or local-only (needing push/build)? *Verify first,
   cheaply, via `scripts/experiments/scratch_modal_grade.py`'s self-check.*
2. **`commit`/snapshot semantics** — does the upstream `Evaluator` rely on a
   `docker commit` between compile and run phases? `ModalContainerEnvironment.
   commit` returns a `modal.Image` snapshot; confirm the evaluator threads it
   through correctly.
3. **cost/steps = 0** — the `CodingAgentAdapter` currently reports `$0 / 0 steps`
   for glm-5.2 (likely `glm-5.2` missing from the pricing table + a turn-count
   mapping gap). Fix so the sweep's numbers are meaningful.
4. **`run_instance` image handling** — its default `pull_image=True` should
   **build-not-pull** (or fail fast) rather than hang on a missing image.

## Out of Scope

- Rewriting ProgramBench grading logic (reuse upstream `Evaluator` verbatim).
- Other cloud backends (Daytona, Northflank) — the `Environment` factory already
  abstracts these; add later.
- Full 201-task tuning — start with the proven subset (the ~9 tasks with
  pre-extracted inputs, plus figlet/errcheck whose images are present).

## References

- Issue #160 (agentic-benchmark goal + full blocker diagnosis), #144 (cloud
  sandboxes).
- `scripts/experiments/scratch_modal_grade.py` — the existing `ModalContainerEnvironment`.
- `scripts/experiments/pb_agentic.py` — the agent-run recipe (`run_instance` + `CodingAgentAdapter`
  + clean packager).
- [coding-agent-harness-integration](coding-agent-harness-integration.md) — the
  shipped adapter/harness/`--agent` work this builds on.
- `chimera/env/modal_sandbox.py`, `chimera/env/factory.py`.
