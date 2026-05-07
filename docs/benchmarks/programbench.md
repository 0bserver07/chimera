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
| Inference loop (call Chimera Agent inside cleanroom container) | TODO — wave-14 |

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

## Follow-up

- Wire a Chimera-Agent-inside-cleanroom-container inference loop. The
  container is at `programbench/<...>:task_cleanroom` and the agent must
  produce a tarball at `submission.tar.gz` matching the upstream layout.
  Recommended preset: a swe-agent-style preset
  (`chimera/agents/presets/swe.py`).
- Add a tarball helper that walks a working tree and emits the required
  `submission.tar.gz` (mirror upstream's `mini-swe-agent` packager).
