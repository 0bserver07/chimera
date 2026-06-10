# Harbor Task Format Adapter

**Date:** 2026-05-28
**Status:** Proposal
**Layer:** 5 (Evaluation)
**Team roles:** `planner` (scope), `executor` (implement), `reviewer` (acceptance), `researcher` (validate against real DeepSWE tasks)
**Depends on:** none (greenfield)
**Unblocks:** [comparative-bench-cli](comparative-bench-cli.md), DeepSWE matrix runs

## Problem

Datacurve's [Harbor](https://www.harborframework.com/docs/tasks) task format is the de facto standard for long-horizon SWE benchmarks. [DeepSWE](https://deepswe.datacurve.ai/) (113 tasks across Go/TS/Python/JS/Rust) ships in Harbor format and other Harbor registry benchmarks will follow. Chimera's eval harness has 24 benchmark adapter modules under `chimera/eval/benchmarks/` but none consume Harbor format. Every new Harbor benchmark today would require a custom adapter; this spec adds one adapter that unlocks the whole format.

## What This Enables

- `chimera bench harbor` runs DeepSWE-113 against any Chimera-replicated agent.
- Future Harbor benchmarks (SWE-Bench-Pro, community contributions) consumed without per-benchmark code.
- Foundation for the comparative matrix story — same Harbor task fed to N agents under controlled budgets.
- Trajectory emission spec ([atif-trajectory-emission](atif-trajectory-emission.md)) can ship Harbor runs to Pier's viewer.

## Harbor Task Format (reference)

Each task is a directory containing:

- `task.toml` — metadata (repository_url, base_commit_hash, language, docker_image, allow_internet, cpus, memory_mb, storage_mb, agent_timeout_sec, verifier_timeout_sec).
- `instruction.md` — prompt shown to the agent verbatim.
- `environment/` — Dockerfile fallback when `docker_image` is unavailable.
- `tests/` — `test.sh` entry point + `test.patch` (applied at grading time, not during the agent's run).
- `solution/` — reference patch (held out from the agent).

Reference: a local clone of the DeepSWE task repository.

## Design Sketch

### HarborTask

```python
@dataclass(frozen=True)
class HarborTask:
    task_id: str
    instruction: str
    repository_url: str
    base_commit_hash: str
    language: str
    docker_image: str
    allow_internet: bool
    cpus: float
    memory_mb: int
    storage_mb: int
    agent_timeout_sec: float
    verifier_timeout_sec: float
    test_sh_path: Path
    test_patch_path: Path
    environment_dir: Path | None
```

### HarborBenchmark

```python
class HarborBenchmark(Benchmark):
    """Benchmark adapter for Harbor-format task directories.

    Args:
        path: Root directory with one subdirectory per task.
        env_factory: Returns an Environment for a given HarborTask.
            Default uses DockerEnvironment(image=task.docker_image,
            cpus=task.cpus, memory_mb=task.memory_mb,
            allow_internet=task.allow_internet).
        seed: Deterministic subset sampling seed.
    """

    def load_tasks(self, n: int | None = None, seed: int = 0) -> list[HarborTask]: ...
    def run_task(self, task: HarborTask, agent: Agent) -> BenchmarkResult: ...
    def verify(self, task: HarborTask, env: Environment) -> bool: ...
```

### Verifier Flow

1. Clone `repository_url` at `base_commit_hash` into sandbox `workdir`.
2. Apply agent's final diff to the working tree.
3. Apply `test.patch` on top (this is the verifier's test additions).
4. Run `test.sh` with `verifier_timeout_sec`.
5. Parse exit code: 0 → pass, non-zero → fail.

### CLI Surface

Wire into `chimera bench` (likely as a `harbor` subcommand):

```bash
chimera bench harbor \
    --path /path/to/deep-swe/tasks \
    --agent swe-agent-replica \
    --model glm-5 \
    -n 10 \
    --seed 0 \
    --output report.json
```

## File Layout

- `chimera/eval/benchmarks/harbor.py` — `HarborTask`, `HarborBenchmark`.
- `chimera/eval/benchmarks/_harbor_verifier.py` — apply `test.patch`, run `test.sh`, parse pass/fail.
- `chimera/cli/bench_harbor.py` — CLI subcommand wiring.
- `tests/eval/benchmarks/test_harbor.py` — unit tests (parser, verifier, subset sampling) against fixture task directories.
- `tests/eval/benchmarks/test_harbor_live.py` — live tests gated by `pytest.importorskip("docker")` against three known-passing DeepSWE tasks.

## Acceptance Criteria

- [ ] Parse every real DeepSWE `task.toml` (113 tasks) without errors.
- [ ] Provision the prebuilt Docker image referenced in `task.toml`.
- [ ] Apply `test.patch` + run `test.sh`, return pass/fail with captured stdout/stderr.
- [ ] Honor `allow_internet=false` (sandbox blocks egress except agent's allowlist if any).
- [ ] Deterministic subset sampling reproduces with same seed.
- [ ] 10-task subset runs end-to-end against a Chimera replica + GLM-5 and emits a `BenchmarkResult`.

## Test Strategy

- **Unit:** fixture task directories under `tests/eval/benchmarks/fixtures/harbor/` covering the schema variants observed in DeepSWE.
- **Live:** `pytest.importorskip("docker")` runs against three DeepSWE tasks confirmed to pass with the oracle agent (no LLM call needed).
- **Smoke:** a single `abs-module-cache-flags` task end-to-end with the `swe-agent` replica.

## Open Questions

- How to surface tasks whose Docker image is unreachable — skip with a clear warning, or fall back to building from `environment/`? Initial choice: fall back, log the slowdown.
- Whether to materialize the repo clone inside the sandbox via `git clone` or via a host-side checkout + bind-mount. Initial choice: clone inside the sandbox for honesty re: `allow_internet`.

## Out of Scope

- Multi-agent comparative runs ([comparative-bench-cli](comparative-bench-cli.md)).
- Trajectory emission in ATIF format ([atif-trajectory-emission](atif-trajectory-emission.md)).
- Building a Harbor task viewer (Pier already has one).
- New benchmark adapters beyond Harbor.

## References

- Mission: see `README.md` and `docs/philosophy.md` — unified benchmark interface.
- Ecosystem: Datacurve ships DeepSWE (benchmark) + Pier (CLI-agent runner) + Harbor (task format); Chimera adopts these formats rather than forking them.
- Harbor docs: <https://www.harborframework.com/docs/tasks>.
- DeepSWE: <https://deepswe.datacurve.ai/>.
