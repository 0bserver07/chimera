---
title: Shrew benchmarks
description: Run Aider Polyglot, GAIA, Harbor, and Terminal-Bench evaluations against a shrew Agent — dataset staging, command-line shape, exit codes, and how to wire your own.
---

# Benchmarks

Shrew ships a small benchmark harness for evaluating small-model
coding capability. Four benchmarks are wired today:

- **Aider Polyglot** — per-language code-edit tasks scored by
  diff-match or test-pass.
- **GAIA** — research-task Q&A scored by GAIA-style answer match.
- **Harbor** — maritime / logistics reasoning tasks scored by
  GAIA-style answer match (see below).
- **Terminal-Bench** — command-line tasks scored by per-task
  verify-command exit code (see below).

The harness lives in
[`chimera/shrew/benchmarks/`](https://github.com/0bserver07/chimera/tree/master/chimera/shrew/benchmarks).

## Why these two

Aider Polyglot exercises **code-editing competence**: can the agent
read a stub file, understand the test, and produce a working
implementation? It's the closest analogue to the day-to-day work
shrew is built for.

GAIA exercises **multi-step reasoning under tool use**: can the
agent decompose a research question, pick the right tool, and
arrive at a single short answer? It catches the failure mode where
small models can edit code but lose the plot on multi-hop
questions.

Together they bracket the small-model coding agent posture: tight
toolbox, real tasks, deterministic scoring.

## Command surface

```bash
chimera shrew bench aider-polyglot --bench-limit 5
chimera shrew bench gaia --bench-limit 5
chimera shrew bench harbor --bench-limit 5
chimera shrew bench terminal-bench --bench-limit 5
```

Flags:

| Flag | Default | Meaning |
|---|---|---|
| `--bench-limit N` | `5` | Max tasks to run; pass `0` for full run. |
| `--model <id>` | `qwen3.6-35b-a3b` | Same shrew model resolution. |
| `--cwd <dir>` | `.` | Working directory for the agent. |

The harness builds a default agent via
`build_shrew_agent_for_eval()`. That helper assembles the full
`AGENT_TOOLS` group (not the small-model `--allowed-tools` subset)
because benchmark runs deliberately use the broadest tool surface
so the model has every chance to succeed; the small-model defaults
bite at production-call time, not eval time.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Benchmark ran and at least one task passed. |
| `1` | Benchmark ran, nothing passed. |
| `2` | Malformed invocation (missing or unknown benchmark name). |
| `3` | Dataset not staged, or runtime failure during the run. |

Exit `3` is the "needs setup" signal. Outer CI scripts can treat
it distinctly from "ran but nothing passed".

## Staging Aider Polyglot

The polyglot benchmark is the Exercism polyglot exercise corpus
plus a per-task index. Shrew **does not** vendor the dataset —
licenses are mixed and we don't ship third-party content.

### Default location

```
~/.chimera/datasets/aider-polyglot/
    tasks.json
    exercises/<id>/
        stub.py
        <id>_test.py
        ...
```

Override via `$CHIMERA_AIDER_POLYGLOT_PATH=/abs/path/to/dir`.

### `tasks.json` schema

A list of task dicts:

```json
[
  {
    "id": "python/hello-world",
    "language": "python",
    "prompt": "Implement the hello() function so the test passes.",
    "expected_files": {
      "hello_world.py": "def hello():\n    return 'Hello, World!'\n"
    },
    "test_command": "pytest -x -q",
    "exercise_dir": "hello-world",
    "timeout_s": 90
  }
]
```

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | Used as task id; should be unique. |
| `language` | no | Threaded into the prompt. |
| `prompt` | yes | Agent prompt body. |
| `expected_files` | no | Diff-match scoring (byte-for-byte). |
| `test_command` | no | Test-pass scoring (subprocess). |
| `exercise_dir` | no | Subdir under `exercises/` to stage. |
| `timeout_s` | no | Test command timeout, default 90. |

When both `expected_files` and `test_command` are present,
`expected_files` wins. When only `test_command` is present,
`evaluate()` runs the command from the staged exercise copy and
passes when the exit code is zero.

### Setup steps

1. Clone the polyglot exercise corpus locally — the upstream Aider
   project hosts a recipe for assembling it from the Exercism
   tracks. Follow their instructions; do **not** vendor it into
   chimera.
2. Author `tasks.json` in the schema above. Start with five tasks,
   confirm the harness runs end-to-end, then expand.
3. Optionally stage exercise trees under `exercises/<id>/`. The
   harness copies these into the agent's working directory at the
   start of each task.

### Run it

```bash
chimera shrew bench aider-polyglot --bench-limit 5
```

When the dataset is missing, shrew prints a setup hint with the
expected path, the env-var override, and a reminder of the schema,
then exits with code `3`.

## Staging GAIA

GAIA is a gated research dataset — you need to accept the dataset
license on Hugging Face before downloading. Shrew **does not**
vendor it.

### Default location

```
~/.chimera/datasets/gaia/
    tasks.json
```

Override via `$CHIMERA_GAIA_PATH=/abs/path/to/dir`.

### `tasks.json` schema

```json
[
  {
    "task_id": "abc-123",
    "Question": "What was the population of ... in 2010?",
    "Final answer": "12345",
    "Level": 1,
    "file_name": "data.xlsx"
  }
]
```

Both `"Question"` / `"question"` and `"Final answer"` /
`"final_answer"` keys are accepted to match the upstream parquet
schema. `Level` is informational; the adapter accepts an optional
`level=` filter.

### Setup steps

1. Accept the GAIA dataset license on Hugging Face.
2. Download the validation set (or test set, if you have access).
3. Convert the parquet to `tasks.json` matching the schema above.
   A one-line conversion is sufficient: pandas → records → json.
4. Drop `tasks.json` at `~/.chimera/datasets/gaia/`.

### Run it

```bash
chimera shrew bench gaia --bench-limit 5
chimera shrew bench gaia --bench-limit 0      # full run (~165 tasks)
```

The default of `--bench-limit 5` is intentional: an unguarded
`shrew bench gaia` would otherwise kick off all 165 validation
tasks on a paid LLM call when the user just wanted to smoke-test.

### Scoring

Shrew re-implements the GAIA scorer locally rather than depending
on an upstream `gaia_scorer` module. The scorer extracts an
`Answer: <value>` line from the agent's final reply and compares
it to the gold using GAIA-style normalisation (accent stripping,
lowercasing, article removal, list/numeric awareness).

If the grader is wrong on a specific task, record the raw answer
and flag it for manual review. **Do not loosen the scorer** —
the GAIA-style normalisation is faithful to the upstream rules; an
incorrect score on a single task is a labelling issue, not a
scorer bug.

## Output shape

The harness prints a one-line summary on stdout:

```text
aider-polyglot: passed=3/5 rate=60.0% cost=$0.0142
gaia: passed=2/5 rate=40.0% cost=$0.0089
```

Per-task event streams persist to
`~/.chimera/eventlog/shrew-<id>/` like any other shrew session.

## Staging Harbor

Harbor is a maritime / logistics reasoning suite — short prompts
asking the agent to compute arrival times, sum manifests, identify
container IDs, and similar deterministic-answer logistics
questions. Like the other shrew benchmarks, shrew **does not**
vendor the dataset.

### Default location

```
~/.chimera/datasets/harbor/
    tasks.json
```

Override via `$CHIMERA_HARBOR_PATH=/abs/path/to/dir`.

### `tasks.json` schema

```json
[
  {
    "task_id": "harbor-001",
    "prompt": "Vessel A arrives at 14:00 and unloads in 30 minutes. When does unloading finish?",
    "answer": "14:30",
    "category": "scheduling",
    "difficulty": 1
  }
]
```

| Key | Required | Meaning |
|---|---|---|
| `task_id` (or `id`) | yes | Used as task id; should be unique. |
| `prompt` (or `question`) | yes | Agent prompt body. |
| `answer` (or `final_answer` / `gold`) | yes | Gold answer (string / number / list). |
| `category` | no | Filter slot (e.g. `scheduling`, `manifest`). |
| `difficulty` | no | Informational; accepts `1` / `2` / `3`. |

### Scoring

Harbor reuses the GAIA scorer so the answer-extraction and
normalisation rules stay in lockstep. The agent is instructed to
end its reply with `Answer: <value>` on its own line; the scorer
extracts that line and compares to the gold using the same
case / accent / punctuation / article folding plus list-set and
numeric-tolerance branches that GAIA uses.

### Run it

```bash
chimera shrew bench harbor --bench-limit 5
```

When the dataset is missing, shrew prints a setup hint with the
expected path, the env-var override, and a reminder of the schema,
then exits with code `3`.

## Staging Terminal-Bench

Terminal-Bench is a suite of command-line tasks: short
instructions asking the agent to set up a tool, fix a config, or
write a shell pipeline inside a fresh working directory. Tasks are
scored by running a per-task **verify** shell command after the
agent finishes — exit code `0` means pass.

The shrew flavour is stdlib-only by design and **does not** depend
on the upstream `terminal-bench` Python package (which pulls
Docker and asciinema). Shrew runs the verify command directly with
`subprocess`. License-respect is the same as every other shrew
benchmark: we do not vendor the dataset.

### Default location

```
~/.chimera/datasets/terminal-bench/
    tasks.json
    tasks/<task-id>/    # optional per-task working tree
        ...             # files referenced by the task instruction
```

Override via `$CHIMERA_TERMINAL_BENCH_PATH=/abs/path/to/dir`.

### `tasks.json` schema

```json
[
  {
    "task_id": "tb-001",
    "instruction": "Find the largest file under /tmp and write its name to result.txt.",
    "verify_command": "test -f result.txt && grep -q '/tmp/' result.txt",
    "task_dir": "tb-001",
    "timeout_s": 60
  }
]
```

| Key | Required | Meaning |
|---|---|---|
| `task_id` (or `id`) | yes | Used as task id; should be unique. |
| `instruction` (or `prompt`) | yes | Agent prompt body. |
| `verify_command` (or `verify`) | yes | Shell command run after the agent; pass on exit code 0. |
| `task_dir` | no | Subdir under `tasks/` to run the verify command from. |
| `timeout_s` | no | Verify timeout in seconds (default 60). |

### Scoring

Exit-code-based. The agent's `output` is **not** parsed —
terminal-bench is side-effect grading. After the agent finishes,
the verify command runs in (in priority order):

1. `env.workdir` if the harness exposes one (the agent's actual
   working tree),
2. `<dataset_root>/tasks/<task_dir>/` if staged,
3. `Path.cwd()` as a last resort.

Pass = exit code `0`. Fail = anything else (non-zero exit, OS
error, or timeout).

### Run it

```bash
chimera shrew bench terminal-bench --bench-limit 5
```

When the dataset is missing, shrew prints a setup hint with the
expected path, the env-var override, and a reminder of the schema,
then exits with code `3`. The legacy "not yet wired" message is
gone — the adapter is wired as of the small-model agent's wave-9
ship.

## Wiring your own benchmark

Inherit from `chimera.eval.harness.Benchmark`:

```python
from chimera.eval.harness import Benchmark, EvalResult

class MyBench(Benchmark):
    def tasks(self) -> list[dict]:
        ...

    def evaluate(self, task, agent_output) -> bool:
        ...
```

Then drive it with the same `Harness` shrew uses:

```python
from chimera.eval.harness import Harness
from chimera.shrew.benchmarks.cli import build_shrew_agent_for_eval

harness = Harness(benchmark=MyBench(...), agent=build_shrew_agent_for_eval())
result = harness.run()
print(result)
```

For the in-tree examples, read
[`chimera/shrew/benchmarks/aider_polyglot.py`](https://github.com/0bserver07/chimera/blob/master/chimera/shrew/benchmarks/aider_polyglot.py)
and
[`chimera/shrew/benchmarks/gaia.py`](https://github.com/0bserver07/chimera/blob/master/chimera/shrew/benchmarks/gaia.py).

## CI integration tips

- **Pin a model.** Set `$SHREW_MODEL` so the harness doesn't
  silently fall back to whatever cloud key happens to be in the CI
  environment.
- **Cache the dataset.** Stage the dataset directory once per CI
  worker and reuse it via the `$CHIMERA_*_PATH` overrides.
- **Treat exit `3` as skip.** The dataset may not be on every
  worker; `3` means "not staged here", which is different from
  "ran and failed". Skip the job rather than failing the build.
- **Record the result line.** The one-line summary is parseable
  (`bench: passed=N/M rate=X% cost=$Y`); pipe it to your CI
  artifact store.

## See also

- [`quickstart.md`](quickstart.md) — first-run walkthrough.
- [`small-model-setup.md`](small-model-setup.md) — getting
  llama.cpp in place before benchmarking.
- [`extensions.md`](extensions.md) — the small-model adjustments
  that bite at runtime; turned off in the eval agent for fair
  comparison.
- [`parity-matrix.md`](parity-matrix.md) — benchmark coverage
  status.
- [`docs/playbooks/07-benchmarking.md`](../playbooks/07-benchmarking.md)
  — generic Chimera benchmarking guidance.
