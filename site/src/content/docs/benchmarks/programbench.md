---
title: "ProgramBench"
---

# ProgramBench

`ProgramBench` (Yang et al., 2026) flips the SWE-bench paradigm. Instead
of asking the agent to patch an existing repository, it gives the agent
**only a compiled binary plus its documentation** and asks the agent to
**rebuild the source from scratch**. Grading is execution-based: the
upstream `programbench eval` CLI runs the agent's submission inside a
per-task Docker container and compares pytest JUnit-XML test outcomes
against the original codebase's test suite.

References:

- HuggingFace: <https://huggingface.co/datasets/programbench/ProgramBench-Tests>
- GitHub: <https://github.com/SWE-agent/ProgramBench>
- Paper: arXiv:2605.03546

## Status: orchestration-only adapter

We **do not re-implement the harness**. We:

1. Load tasks from the upstream `tasks/` directory layout (or a JSON dump).
2. Stage the agent's `submission.tar.gz` under
   `<run_dir>/<instance_id>/submission.tar.gz`.
3. Shell out to `programbench eval <run_dir>` to grade.
4. Parse the resulting `<instance_id>.eval.json` and return pass/fail.

| Surface | State |
| --- | --- |
| Task loader (tasks/*/task.yaml + JSON / JSON-lines dump) | DONE |
| Instance + filter API (language, difficulty, limit, breakdowns) | DONE |
| Cleanroom Docker image-name derivation | DONE |
| `programbench eval` orchestration | DONE |
| `<id>.eval.json` parser | DONE |
| Skip when Docker missing or non-`linux/amd64` host | DONE |
| Live integration test (gated on `CHIMERA_PROGRAMBENCH_LIVE=1`) | DONE |
| Discoverable via `chimera eval --benchmark programbench` | DONE |
| Inference loop (`ProgramBench.run_instance`) | DONE — wave-14 |

## Quick start

```python
from chimera.eval.benchmarks import ProgramBench

bench = ProgramBench(
    tasks_dir="/path/to/ProgramBench/src/programbench/data/tasks",
    language="rust",
    limit=5,
    run_dir="./pb-runs/baseline-glm5",
)
print(bench.name())                     # "programbench-rust"
print(bench.language_breakdown())       # {'rust': 5}

# Grade an existing submission tarball
ok = bench.evaluate(bench.tasks()[0], "/path/to/submission.tar.gz")
```

## Image naming convention

The upstream Docker images replace `__` with `_1776_`:

| Instance ID | Cleanroom image |
| --- | --- |
| `abishekvashok__cmatrix.5c082c6` | `programbench/abishekvashok_1776_cmatrix.5c082c6:task_cleanroom` |
| `agourlay__zip-password-finder.704700d` | `programbench/agourlay_1776_zip-password-finder.704700d:task_cleanroom` |

`ProgramBenchInstance.cleanroom_image(tag=...)` returns the full
`programbench/<derived>:<tag>` reference.

## Skip pattern

`ProgramBench.evaluate` calls `check_runtime_or_skip()` which raises
`BenchmarkSkipped` when:

- Docker is not on PATH, or `docker version` fails.
- The host is not `linux/amd64` (the upstream images are x86_64-only).

To force a run on a non-native host (slow QEMU emulation), set:

```bash
export CHIMERA_PROGRAMBENCH_LIVE=1
```

This also enables the gated `tests/eval/test_programbench.py::TestLiveIntegration`
smoke test.

## Output schema

The CLI writes `<run_dir>/<instance_id>/<instance_id>.eval.json`. We
expose a small parser:

```python
from chimera.eval.benchmarks.programbench import parse_eval_json

summary = parse_eval_json("./pb-runs/baseline/o__r.abc/o__r.abc.eval.json")
# {'passed': 12, 'total': 14, 'branches': 2, 'error_code': None, 'warnings': []}
```

`evaluate` returns `True` only when `passed == total > 0`. Partial passes
need the `parse_eval_json` summary directly — they are not folded into
the headline boolean.

## Running inference

The wave-14 inference loop is `ProgramBench.run_instance`. It pulls the
cleanroom Docker image, extracts the binary + docs into a fresh
workspace, drives a Chimera `Agent` against the rebuild prompt, and
packages the workspace into the `submission.tar.gz` the upstream
`programbench eval` CLI expects.

```python
from pathlib import Path

from chimera.agents.config import AgentConfig
from chimera.eval.benchmarks import ProgramBench
from chimera.providers.factory import create_provider

bench = ProgramBench(
    tasks_dir="/path/to/ProgramBench/src/programbench/data/tasks",
    language="rust",
    limit=5,
    run_dir="./pb-runs/baseline-glm5",
)

# Build a swe-agent-style agent for each instance.
SWE_PRESET = AgentConfig.from_markdown(
    "chimera/agents/presets/swe-agent.md"
)


def make_agent(instance, workspace):
    provider = create_provider(model="glm-5")
    return SWE_PRESET.build(provider)


for task in bench.tasks():
    result = bench.run_instance(
        task,
        workspace=Path(f"./pb-runs/baseline-glm5/{task['id']}/ws"),
        agent_factory=make_agent,
    )
    print(task["id"], result.success, result.cost, result.submission_tar)

    # Defer to upstream `programbench eval` for grading
    bench.evaluate(task, str(result.submission_tar))
```

### What `run_instance` does

1. Calls `check_runtime_or_skip()` — same skip semantics as `evaluate`.
2. Calls `pull_cleanroom_image(image_ref)` (`docker pull <image>`).
3. Calls `extract_cleanroom_artifacts(image_ref, workspace/_inputs)` —
   uses `docker create` + `docker cp` + `docker rm` to copy the
   binary and docs out of the image without keeping a container alive.
4. Resolves an `Agent` (either the `agent=` kwarg or the
   `agent_factory(instance, workspace)` callback).
5. Calls `agent.run(prompt, env=LocalEnvironment(workspace))` with a
   prompt rendered by `build_rebuild_prompt` (mentions the workspace
   path, instance metadata, the no-internet rule, and the
   `_inputs/`-is-spec-not-source rule).
6. Calls `package_submission(workspace, workspace/submission.tar.gz)`
   to gzip-tar everything in `workspace/` *except* `_inputs/` and
   the tarball itself.

### Mocking for tests

Every external call is injectable:

| kwarg | default | purpose |
| --- | --- | --- |
| `image_puller` | `pull_cleanroom_image` | swap in for a no-op in tests |
| `artifact_extractor` | `extract_cleanroom_artifacts` | populate `_inputs/` from a fixture |
| `submission_packager` | `package_submission` | use a custom tar layout |
| `pull_image=False` | — | skip the docker pull entirely |
| `extract_artifacts=False` | — | skip extraction |
| `runtime_check=False` | — | skip the docker/amd64 gate |

A live test gated on `CHIMERA_PROGRAMBENCH_LIVE=1` lives in
`tests/eval/test_programbench_inference.py::TestLiveInference`.

### Result shape

`ProgramBench.run_instance` returns a `ProgramBenchRunResult`:

| field | type | description |
| --- | --- | --- |
| `instance_id` | `str` | task id |
| `submission_tar` | `Path` | path to `submission.tar.gz` (always produced, even on agent failure) |
| `workspace` | `Path` | the directory the agent wrote into |
| `agent_result` | `AgentResult \| None` | the `Agent.run` return value |
| `steps` | `int` | mirrored from `agent_result.steps` |
| `cost` | `float` | mirrored from `agent_result.cost` |
| `success` | `bool` | the agent's self-reported success — *not* the benchmark score |
| `error` | `str \| None` | exception summary if `Agent.run` raised |

The benchmark score still requires `bench.evaluate(task,
result.submission_tar)`, which shells out to the upstream CLI.

## Follow-up

- Optional `mode="container"` flag on `run_instance` — wire a
  `DockerEnvironment` rooted at the cleanroom image so the agent's
  bash/edit tools execute *inside* the container. The current default
  uses a `LocalEnvironment` and only enters Docker for image pull
  and artifact extraction.
- Aggregate harness loop that feeds `bench.tasks()` through
  `run_instance` + `evaluate` and emits a JSONL run report.
