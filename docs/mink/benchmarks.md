# Benchmarks

Benchmark adapters that ship with Chimera and can be driven by the
evaluation harness (`chimera/eval/harness.py`).

## Overview

A benchmark in Chimera is a `Benchmark` subclass (`chimera/eval/harness.py`)
exposing three methods: `name()`, `tasks()`, `evaluate(task, output, env)`.
The `Harness` runs an agent against every task, optionally per-task in a
fresh `Environment`, then aggregates pass rate, total cost, and per-task
results into an `EvalResult`.

Adapter status is one of:

- **validated** — adapter has unit tests and/or a recorded GLM-5/GLM-5.1
  baseline in `data/`.
- **scaffolded** — adapter shape is in place (loader, `tasks()`,
  `evaluate()`) but has not been driven against a real dataset / Docker
  harness in this repo. Follow-up issue tracks the gap.

Status below was reconstructed from `research/mink/A{9,10,11,14,17}-REPORT.md`
(the reports that landed before the polling cutoff), source files,
`chimera/eval/benchmarks/__init__.py`, and the GitHub issue comments on
#84-#96.

## Per-benchmark summary

| Benchmark              | Issue | Status      | File                                                  | Baseline / Notes                                  |
|------------------------|-------|-------------|-------------------------------------------------------|---------------------------------------------------|
| SWE-bench Verified     | #84   | scaffolded  | `chimera/eval/benchmarks/swe_bench.py`                | 10% on Lite (GLM-5.1, 20 smallest patches)        |
| Terminal-Bench 2.0     | #85   | validated   | `chimera/benchmarks/terminal_bench_agent.py`          | 30% (3/10) GLM-5; follow-up #139                  |
| FeatureBench           | #86   | scaffolded  | `chimera/eval/benchmarks/feature_bench.py`            | needs HF dataset + Docker images                  |
| Cline Bench            | #87   | scaffolded  | `chimera/eval/benchmarks/cline_bench.py`              | needs RL container images                         |
| DPAI Arena             | #88   | scaffolded  | `chimera/eval/benchmarks/dpai_arena.py`               | Java/Spring; six tracks; no baseline              |
| SWT-Bench              | #89   | scaffolded  | `chimera/eval/benchmarks/swt_bench.py`                | 15 unit tests pass; needs Docker + C metric       |
| tau-bench              | #90   | scaffolded  | `chimera/eval/benchmarks/tau_bench.py`                | needs `tau2-bench` upstream package               |
| Context-Bench (Letta)  | #91   | scaffolded  | `chimera/eval/benchmarks/context_bench.py`            | needs Letta evals dataset                         |
| SWE-PolyBench          | #92   | scaffolded  | `chimera/eval/benchmarks/swe_polybench.py`            | needs HF dataset + JS/TS/Java toolchains          |
| HumanEval+             | #93   | scaffolded  | `chimera/eval/benchmarks/humaneval_plus.py`           | needs `evalplus` extras                           |
| MBPP                   | #94   | scaffolded  | `chimera/eval/benchmarks/mbpp.py`                     | local JSON loader; sanitized split recommended    |
| LiveCodeBench          | #95   | scaffolded  | `chimera/eval/benchmarks/livecodebench.py`            | date-window filter for contamination control      |
| MATH-500 / AIMO        | #96   | scaffolded  | `chimera/eval/benchmarks/math500.py`, `aimo.py`       | AIMO has live-LLM tests; MATH-500 loader-only     |
| HumanEval (base)       | n/a   | validated   | `chimera/eval/benchmarks/human_eval.py`               | 66.5% (109/164) GLM-5.1; raw in `data/`           |
| Custom                 | n/a   | validated   | `chimera/eval/benchmarks/custom.py`                   | user-defined tasks; in-tree tests                 |

Issue links: `https://github.com/0bserver07/chimera/issues/<N>`.

## Per-benchmark detail

### SWE-bench (#84)

Real GitHub issues with test verification. `SWEBench` loads
`SWEBenchInstance` records from JSON / JSONL (or the
`{"tasks": [...]}` wrapper) and evaluates by applying `test_patch`
in the supplied environment, then running `env.run_tests()`.

- File: `chimera/eval/benchmarks/swe_bench.py`
- Tests: `tests/eval/test_swe_bench.py` (11 unit tests), `tests/eval/test_bench_swe.py`
- Baseline: 10% (2/20) on SWE-bench Lite, 20 smallest patches, GLM-5.1.
  Raw in `data/swebench-lite-glm51-results.jsonl`.
- Run: `chimera eval --benchmark swe-bench --dataset path/to/instances.jsonl`
- Full run example: `examples/benchmarks/swe_bench_proper.py`,
  `examples/benchmarks/swe_bench_docker.py`.

### Terminal-Bench 2.0 (#85)

Containerised terminal tasks under `tb`. Chimera wraps tasks as a
`ChimeraAgent(BaseAgent)` thin ReAct loop that drives a TmuxSession
through `provider.complete()`.

- File: `chimera/benchmarks/terminal_bench_agent.py` (168 LoC)
- Baseline: 30% (3/10) GLM-5, 2026-03-20. See
  `docs/benchmarks/2026-03-30-terminal-bench-glm5.md`.
- Follow-up: issue #139 lists adaptive-wait, `max_turns` 30 -> 50,
  richer system prompt, error recovery, swap to `claude_code` preset.
- Run: requires `pip install terminal-bench` and Docker; invoke via
  `tb run --agent chimera ...` once configured.

### FeatureBench (#86)

End-to-end Python feature development with a test-driven grader.

- File: `chimera/eval/benchmarks/feature_bench.py`
- Loader: local JSON / JSONL plus opt-in `load_from_hub('LiberCoders/FeatureBench')`.
- `evaluate()` chains `env.run_tests(test_files)` -> `env.run_command('python -m pytest -x ...')`
  -> non-empty-output fallback.
- Status: scaffolded only; needs HF dataset pull and ~13 Docker images.
- Run: `uv run python -c "from chimera.eval.benchmarks import FeatureBench; b = FeatureBench(dataset_path='...'); ..."`.

### Cline Bench (#87)

Real-world engineering tasks from Cline user sessions, packaged as
Docker RL environments with binary test-suite graders.

- File: `chimera/eval/benchmarks/cline_bench.py`
- Loader: directory of per-task JSON, single JSON file, or JSONL.
- Status: scaffolded only; needs the upstream `cline/cline-bench`
  task definitions and container images.

### DPAI Arena (#88)

JetBrains Developer Productivity AI Arena: Java/Spring tasks across
six tracks (`issue-to-patch`, `pr-review`, `coverage`,
`static-analysis`, `upgrade`, `compliance`).

- File: `chimera/eval/benchmarks/dpai_arena.py`
- Status: scaffolded only; needs the Spring task corpus and
  per-track grader wiring.

### SWT-Bench (#89)

Test-generation analogue of SWE-bench: agent must produce tests that
fail on the buggy base and pass after the gold patch.

- File: `chimera/eval/benchmarks/swt_bench.py`
- Modes: `unit_test` (integrate into suite), `reproduction` (script
  exit codes).
- Tests: `tests/eval/test_bench_swt.py` (15 tests, all passing).
- Status: F2P contract enforced in-process; deferred work covers
  Change-Coverage (C) metric, predictions JSONL writer, and Docker
  smoke run on the Lite subset.

### tau-bench (#90)

Multi-turn tool-use and conversational agent evaluation across
airline / retail / telecom / banking domains. Stateful: end-state
DB is compared against the annotated goal; reliability is `pass^k`.

- File: `chimera/eval/benchmarks/tau_bench.py`
- Status: scaffolded only; full execution requires the upstream
  `tau2-bench` package for simulated environments and user simulator.

### Context-Bench (#91)

Letta long-running-context benchmark. Programmatic SQL-derived
questions over a fictional-entity database; agent must navigate
semi-structured text files with grep/open-style tools.

- File: `chimera/eval/benchmarks/context_bench.py`
- Suites: `filesystem` (default), `skills`.
- Status: scaffolded only; lazy-loads the Letta evals framework and
  falls back to a user-supplied JSON dataset offline.

### SWE-PolyBench (#92)

Multi-language repository-level benchmark (Python / Java / JS / TS).

- File: `chimera/eval/benchmarks/swe_polybench.py`
- Filters: `split` in {`full`, `pb500`, `verified`}, `language` in
  {`python`, `java`, `javascript`, `typescript`}, `limit`.
- `evaluate()` applies `test_patch` then runs the language-appropriate
  command (`pytest -x`, `npm test --silent`, `mvn -q test`).
- Extra metrics: `localization_accuracy()` (file-level recall),
  `cst_node_recall()` (CST-node recall, paper-specific).
- Status: scaffolded only; needs HF dataset dump and JS/TS/Java
  toolchain images.

### HumanEval+ (#93)

EvalPlus extension to HumanEval with ~80x more test cases per
problem; exposes brittle solutions.

- File: `chimera/eval/benchmarks/humaneval_plus.py`
- Status: scaffolded only; pulls from the optional `evalplus`
  package when installed, falls back to local JSONL otherwise.

### MBPP (#94)

974-problem entry-level Python benchmark; sanitized split is 427
hand-verified problems.

- File: `chimera/eval/benchmarks/mbpp.py`
- Loader: local JSON / JSONL only (zero-dependency core; no HF import).
- `evaluate()` runs the `test_list` asserts in-process or via
  `env.run_command`.
- Status: scaffolded only; needs a downloaded MBPP dataset file.

### LiveCodeBench (#95)

Contamination-controlled competitive-programming benchmark from
LeetCode / AtCoder / CodeForces. Each problem is timestamped so
evaluation can restrict to post-cutoff problems.

- File: `chimera/eval/benchmarks/livecodebench.py`
- Date-window helpers: `LiveCodeBench(start_date=..., end_date=...)`,
  `LiveCodeBench.rotated_window(model_cutoff=..., months=3)`.
- Scenarios: `codegeneration` wired; `selfrepair`, `codeexecution`,
  `testoutput` raise `NotImplementedError` until the upstream JSON
  schema is wired in.

### MATH-500 / AIMO (#96)

Mathematical reasoning. AIMO is the AI Mathematical Olympiad
adapter; MATH-500 is the 500-problem subset of MATH covering
seven competition-math subjects.

- Files: `chimera/eval/benchmarks/aimo.py`,
  `chimera/eval/benchmarks/math500.py`
- Tests: `tests/eval/test_bench_aimo.py`, `tests/eval/test_aimo_integration.py`
  (latter is live-LLM).
- AIMO answer extraction handles `ANSWER: <n>`, `\boxed{<n>}`, and
  trailing-integer fallback.
- MATH-500 evaluator does normalised string equivalence first, then
  optional `sympy` symbolic equivalence when installed.
- Run: `chimera eval --benchmark aimo --dataset path/to/aimo.json`.

### HumanEval (base, validated)

Original HumanEval — 164 hand-written Python problems.

- File: `chimera/eval/benchmarks/human_eval.py`
- Tests: `tests/eval/test_bench_human_eval.py`
- Baseline: 66.5% pass@1 (109/164), GLM-5.1. Raw in
  `data/humaneval-glm51-results.json`. (Earlier 90.9% GLM-5 figure
  from project memory predates the recorded raw data.)
- Run: `chimera eval --benchmark human-eval --dataset path/to/humaneval.json`.

### Custom (validated)

User-defined task list or directory of task JSON. Useful for
one-off harness runs and integration smoke tests.

- File: `chimera/eval/benchmarks/custom.py`
- Tests: `tests/eval/test_bench_custom.py`
- Run: `chimera bench --suite custom --tasks-dir path/to/tasks/`.

## Running a benchmark

CLI front door (registered names: `human-eval`, `humaneval`,
`swe-bench`, `swebench`, `aimo`, `custom`):

```bash
chimera eval --benchmark swe-bench --dataset path/to/instances.jsonl --limit 10 --output results.json
chimera bench --suite custom --tasks-dir path/to/tasks/ --output results.json
```

The scaffolded adapters above (`feature_bench`, `cline_bench`, `dpai_arena`,
`swt_bench`, `tau_bench`, `context_bench`, `swe_polybench`, `humaneval_plus`,
`mbpp`, `livecodebench`, `math500`) are not yet wired into `_BENCHMARKS`
in `chimera/cli/main.py`. Drive them directly through the harness:

```bash
uv run python - <<'PY'
from chimera.eval.benchmarks import SWTBench
from chimera.eval.harness import Harness

bench = SWTBench(dataset_path="path/to/swt.jsonl", mode="unit_test")
# harness = Harness(benchmark=bench, agent=my_agent, env_factory=my_env_factory)
# print(harness.run().pass_rate)
PY
```

To add an adapter to the CLI, append an entry to `_BENCHMARKS` in
`chimera/cli/main.py` and update `_load_benchmark` if its constructor
takes anything beyond `dataset_path` / `limit`.

## Adding your own benchmark

Subclass `chimera.eval.harness.Benchmark` and implement three methods:

```python
from chimera.eval.harness import Benchmark

class MyBench(Benchmark):
    def name(self) -> str: ...
    def tasks(self) -> list[dict]: ...                 # each task needs at least 'id', 'prompt'
    def evaluate(self, task, agent_output, env) -> bool: ...
```

`tasks()` should return dicts shaped for whatever `Agent.run(prompt, env)`
your harness uses. `evaluate()` receives the original task dict, the
agent's stringified output, and (optionally) the per-task `Environment`.

Drop the file under `chimera/eval/benchmarks/`, export from
`chimera/eval/benchmarks/__init__.py`, and wire into the CLI map if you
want a `chimera eval --benchmark <name>` shortcut. See the SWE-bench
adapter (`chimera/eval/benchmarks/swe_bench.py`) for a complete reference
implementation, and `chimera/eval/benchmarks/README.md` for additional
notes on the SWE-bench scaffold.
