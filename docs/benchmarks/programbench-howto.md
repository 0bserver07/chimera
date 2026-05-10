---
title: "ProgramBench: running locally"
description: "Step-by-step guide to running ProgramBench locally — Docker setup, task loading, agent inference, and grading."
---

This page walks through the practical steps to run [ProgramBench](./programbench.md) on your own machine. ProgramBench inverts SWE-bench: instead of patching a repo, the agent **rebuilds source from scratch** given only a compiled binary and its docs. Grading happens inside per-task Docker images via the upstream `programbench eval` CLI.

For the conceptual overview and adapter status, see the [ProgramBench reference page](./programbench.md). This page is the howto.

## Prerequisites

- **OS** — `linux/amd64`. The upstream cleanroom Docker images are x86_64-only. On Apple Silicon or Windows, you'll need Rosetta or QEMU emulation (slow), or use a remote linux/amd64 host.
- **Docker** — installed and running. `docker version` must succeed.
- **The upstream ProgramBench repo** — clone it locally:
  ```bash
  git clone https://github.com/SWE-agent/ProgramBench.git
  ```
- **The `programbench` CLI** — install per the upstream README (typically `pip install -e .` in the cloned repo, but check upstream for the current recipe).
- **Chimera** — `pip install chimera-run` plus a provider extra (e.g. `pip install chimera-run[anthropic]`).
- **A model** — `glm-5`, `deepseek-v4-pro`, or any other coding-capable model wired into Chimera.

## Skip behaviour

Chimera's adapter calls `check_runtime_or_skip()` before every task. It raises `BenchmarkSkipped` when:

- Docker is not on PATH, or `docker version` fails.
- The host is not `linux/amd64`.

To force a run on a non-native host (slow QEMU emulation), set:

```bash
export CHIMERA_PROGRAMBENCH_LIVE=1
```

The same env var enables the gated `tests/eval/test_programbench.py::TestLiveIntegration` smoke test.

## Step 1: Load the tasks

```python
from chimera.eval.benchmarks import ProgramBench

bench = ProgramBench(
    tasks_dir="/path/to/ProgramBench/src/programbench/data/tasks",
    language="rust",
    limit=5,
    run_dir="./pb-runs/baseline-glm5",
)
print(bench.name())                # "programbench-rust"
print(bench.language_breakdown())  # {'rust': 5}
print(len(bench.tasks()))          # 5
```

Filtering options:

| kwarg | type | effect |
|---|---|---|
| `language` | `str \| None` | restrict to one language (`"rust"`, `"python"`, `"c"`, etc.) |
| `difficulty` | `str \| None` | restrict by difficulty tier |
| `limit` | `int \| None` | cap the number of tasks loaded |

`tasks_dir` can be the upstream `tasks/` tree or a JSON / JSON-lines dump.

## Step 2: Run inference

```python
from pathlib import Path
from chimera.agents.config import AgentConfig
from chimera.providers.factory import create_provider

SWE_PRESET = AgentConfig.from_markdown(
    "chimera/agents/presets/swe-agent.md"
)

def make_agent(instance, workspace):
    provider = create_provider(model="glm-5")
    return SWE_PRESET.build(provider)

for task in bench.tasks():
    workspace = Path(f"./pb-runs/baseline-glm5/{task['id']}/ws")
    result = bench.run_instance(
        task,
        workspace=workspace,
        agent_factory=make_agent,
    )
    print(task["id"], result.success, f"${result.cost:.4f}", result.submission_tar)
```

`run_instance` does six things:

1. Calls `check_runtime_or_skip()` (Docker + linux/amd64 gate).
2. Pulls the cleanroom image (`docker pull programbench/<derived>:task_cleanroom`).
3. Extracts the binary and docs into `workspace/_inputs/` via `docker create` + `docker cp` + `docker rm`.
4. Resolves an agent (the `agent=` kwarg or the `agent_factory` callback).
5. Calls `agent.run(prompt, env=LocalEnvironment(workspace))` with a prompt built by `build_rebuild_prompt` (mentions the workspace path, the no-internet rule, and the `_inputs/`-is-spec-not-source rule).
6. Packages everything in `workspace/` (except `_inputs/` and the tarball) into `workspace/submission.tar.gz`.

## Step 3: Grade

```python
ok = bench.evaluate(task, str(result.submission_tar))
print(task["id"], "passed" if ok else "failed")
```

`evaluate` shells out to `programbench eval <run_dir>`, then parses `<run_dir>/<instance_id>/<instance_id>.eval.json`:

```python
from chimera.eval.benchmarks.programbench import parse_eval_json

summary = parse_eval_json(
    "./pb-runs/baseline-glm5/o__r.abc/o__r.abc.eval.json"
)
# {'passed': 12, 'total': 14, 'branches': 2, 'error_code': None, 'warnings': []}
```

## Image-naming convention

Cleanroom images replace `__` with `_1776_`:

| Instance ID | Cleanroom image |
|---|---|
| `abishekvashok__cmatrix.5c082c6` | `programbench/abishekvashok_1776_cmatrix.5c082c6:task_cleanroom` |
| `agourlay__zip-password-finder.704700d` | `programbench/agourlay_1776_zip-password-finder.704700d:task_cleanroom` |

`ProgramBenchInstance.cleanroom_image(tag=...)` returns the full reference.

## Mocking for tests

Every external call is injectable, so unit tests run without Docker:

| kwarg | default | purpose |
|---|---|---|
| `image_puller` | `pull_cleanroom_image` | swap in a no-op |
| `artifact_extractor` | `extract_cleanroom_artifacts` | populate `_inputs/` from a fixture |
| `submission_packager` | `package_submission` | use a custom tar layout |
| `pull_image=False` | — | skip the docker pull entirely |
| `extract_artifacts=False` | — | skip extraction |
| `runtime_check=False` | — | skip the docker/amd64 gate |

A live test gated on `CHIMERA_PROGRAMBENCH_LIVE=1` lives at `tests/eval/test_programbench_inference.py::TestLiveInference`.

## End-to-end CLI

```bash
chimera eval --benchmark programbench \
  --model glm-5 \
  --tasks-dir /path/to/ProgramBench/src/programbench/data/tasks \
  --language rust \
  --limit 5 \
  --run-dir ./pb-runs/baseline-glm5
```

## Troubleshooting

- **`BenchmarkSkipped: docker not found`** — install Docker Desktop or the engine; verify with `docker version`.
- **`BenchmarkSkipped: host arch <other>`** — switch to a linux/amd64 host, or set `CHIMERA_PROGRAMBENCH_LIVE=1` to force a slow QEMU run.
- **Image pull fails** — check the cleanroom image is published and your Docker is logged in if the registry is private.
- **`programbench eval` not found** — install the upstream CLI per its README; verify with `which programbench`.

## See also

- [ProgramBench reference](./programbench.md) — the conceptual overview and adapter status.
- [Use DeepSeek-V4](../guides/use-deepseek-v4.md) — alternative model wiring for the inference step.
