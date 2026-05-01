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
| SWE-bench Lite         | #84   | scaffolded  | `chimera/eval/benchmarks/swe_bench.py`                | 10% (2/20) GLM-5.1, 20 smallest patches           |
| SWE-bench Verified     | #84   | scaffolded  | `chimera/eval/benchmarks/swe_bench_verified.py`       | adapter + 500-step / IPython / condense plumbing  |
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
| WebArena               | n/a   | scaffolded  | `chimera/eval/benchmarks/webarena.py`                 | string_match + url_match; no upstream sandbox     |
| HumanEval (base)       | n/a   | validated   | `chimera/eval/benchmarks/human_eval.py`               | 66.5% (109/164) GLM-5.1; raw in `data/`           |
| Aider Polyglot         | n/a   | scaffolded  | `chimera/eval/benchmarks/aider_polyglot.py`           | 6 langs; diff-match + test-cmd; shrew wrapper     |
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

### SWE-bench Verified (#84, dedicated adapter)

The Verified split is a 500-task human-validated subset of SWE-bench
Full with cleaner problem statements and deterministic test
specifications. The dataset schema is identical to Lite (so the loader
inherits from `SWEBench`); the differences live in the *agent
configuration* the adapter recommends.

- File: `chimera/eval/benchmarks/swe_bench_verified.py`
- Tests: `tests/eval/benchmarks/test_swe_bench_verified.py` (24 unit
  tests covering variant config, max-step plumbing, IPython tool
  surface, condensation trigger).
- Baseline: not yet run live. Lite baseline (10%) is the reference
  point; Verified live run is open follow-up under issue #84.
- Configuration knobs (with their defaults):
    - `max_steps=500` — Verified default. Lite default is 100. The
      step budget is exposed as `bench.max_steps` and as
      `bench.config.max_steps` for callers to plug into a `LoopConfig`.
    - `ipython=True` — when set, `bench.build_ipython_tool()` returns
      a `chimera.tools.ipython.IPythonTool` instance. The tool wraps a
      stateful `ipython --no-banner` (or `python -i -u` fallback)
      subprocess so variables, imports, and instrumentation persist
      across tool calls. Each session is single-threaded; supply a
      fresh tool per task for clean state.
    - `condense_every_n_steps=25` — every N steps the agent loop
      should call `bench.should_condense(step)`; when it returns
      `True`, run `bench.build_condensation(provider=...)` to get a
      `SummaryCompaction` and apply it to the message log. `0`
      disables condensation entirely (matching Lite behavior).
- Helpers: `SWEBenchConfig.for_lite(...)` and
  `SWEBenchConfig.for_verified(...)` build the recommended runtime
  config for callers that don't want to subclass.
- Run (loader only — Docker still required for live eval):
  ```python
  from chimera.eval.benchmarks import SWEBenchVerified

  bench = SWEBenchVerified(
      dataset_path="path/to/swe-bench-verified.jsonl",
      max_steps=500,
      ipython=True,
      condense_every_n_steps=25,
  )
  for task in bench.tasks():
      ...  # drive the agent; max_steps/IPython/condense via bench.config
  ```
- Status: scaffolded only — adapter, config, IPython tool, and the
  `should_condense` trigger are wired and unit-tested. A live run on
  the Verified Docker harness is the next milestone.

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
- Tests: `tests/eval/benchmarks/test_tau_bench.py` (39 tests, dataset-absent
  skip path + scoring logic).
- Status: wired. Full simulated-environment execution still requires
  the upstream `tau-bench` / `tau2-bench` package — we do **not**
  vendor or pip-install upstream. The adapter loads task definitions
  from a local directory, normalises them, and scores in-process via
  terminal-action match (with a `goal_state` fallback when present).
  When an upstream `env` exposing `evaluate_task(task, output)` is
  passed in, the adapter delegates to it.

#### Setup

```bash
# 1. Clone upstream tasks (read-only — we never pip install it):
git clone https://github.com/sierra-research/tau-bench /tmp/tau-bench

# 2. Stage the JSON task dumps under the default dataset dir:
mkdir -p ~/.chimera/datasets/tau-bench
cp /tmp/tau-bench/tau_bench/envs/retail/tasks_train.json \
   ~/.chimera/datasets/tau-bench/retail_train.json
cp /tmp/tau-bench/tau_bench/envs/airline/tasks.json \
   ~/.chimera/datasets/tau-bench/airline.json

# 3. Smoke-run the adapter:
uv run python -m chimera.eval.benchmarks.tau_bench --limit 3 --domain airline
```

Override the dataset directory with `CHIMERA_TAU_BENCH_PATH=/path/to/dir`.

#### CLI flags

| Flag         | Default       | Description                                              |
|--------------|---------------|----------------------------------------------------------|
| `--domain`   | `airline`     | One of `airline`, `retail`, `telecom`, `banking`, `mock`.|
| `--limit`    | `3`           | Maximum tasks to run.                                    |
| `--model`    | `glm-5`       | Provider model id passed to `create_provider()`.         |
| `--dataset`  | env / default | Override the dataset path (file or directory).          |
| `--no-color` | off           | Disable ANSI colour in the results table.                |

When the dataset is absent the CLI prints a friendly setup hint and
exits with status 2 — safe to wire into CI smoke gates.

#### Scoring

The in-process evaluator matches the agent's **terminal action**
(name + arguments) against the annotated `actions[-1]` from the task
JSON. This mirrors the upstream tau-bench convention: only the final
mutating call needs to match, since that's the call that drives the
database into the goal state. Two acceptable agent output shapes:

```json
{"actions": [{"name": "cancel_reservation", "arguments": {"id": "r1"}}]}
```

or, when the task carries a `goal_state` field:

```json
{"final_state": {"reservations": []}}
```

Plain-text outputs are scored leniently against the terminal action
name (substring match) — useful for early scaffold runs before the
agent reliably emits structured tool-call traces.

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

### WebArena

Web-agent benchmark: realistic tasks across self-hosted e-commerce,
GitLab, CMS, and Reddit sandbox sites. Each task carries an `intent`
(natural-language goal), `start_url`, and an `eval_types` list
declaring how success is judged: `string_match` (against a
`reference_answer` or compound `reference_answers` map),
`url_match` (against `reference_url`), and `program_html`
(programmatic DOM checks).

- File: `chimera/eval/benchmarks/webarena.py`
- Tests: `tests/eval/benchmarks/test_webarena.py` (47 unit tests
  covering dataset-absent skip, JSON / JSONL load, scoring round-trip
  for both `string_match` and `url_match`, compound
  `reference_answers`, combined eval-types AND semantics, and the
  upstream `env` escape hatch).
- Status: scaffolded — `string_match` + `url_match` scorers are wired
  in-process. `program_html` is recognised but deferred (fails closed
  so a stub never falsely scores). Full execution requires the
  upstream sandbox sites (Docker — heavyweight) plus the upstream
  `webarena` package for DOM/accessibility observations. We do **not**
  vendor or pip-install upstream — the licence on the task corpus is
  unclear.

#### Setup

```bash
# 1. Clone upstream tasks (read-only — we never pip install it):
git clone https://github.com/web-arena-x/webarena /tmp/webarena

# 2. Stage the JSON task config dump under the default dataset dir:
mkdir -p ~/.chimera/datasets/webarena
cp /tmp/webarena/config_files/test.raw.json \
   ~/.chimera/datasets/webarena/test.json

# 3. Stand up the upstream sandbox sites (Docker, heavyweight):
#    https://github.com/web-arena-x/webarena/blob/main/environment_docker/README.md
```

Override the dataset directory with `CHIMERA_WEBARENA_PATH=/path/to/dir`.

#### Scoring

The agent's output may be either a JSON envelope
`{"answer": "...", "url": "..."}` or two named lines:

```
ANSWER: Widget Pro Max
URL: http://shop.example.test/p/widget-pro-max
```

`url_match` compares scheme + netloc + path (query + fragment
ignored, trailing slash normalised). `string_match` lowercases and
collapses whitespace before comparing; the upstream
`reference_answers.must_include` / `fuzzy_match` / `exact_match`
shape is honoured. When multiple eval types are declared, **all**
must pass.

When an upstream `env` exposing `evaluate_task(task, output)` is
passed in, the adapter delegates to it — same escape hatch as
tau-bench.

### HumanEval (base, validated)

Original HumanEval — 164 hand-written Python problems.

- File: `chimera/eval/benchmarks/human_eval.py`
- Tests: `tests/eval/test_bench_human_eval.py`
- Baseline: 66.5% pass@1 (109/164), GLM-5.1. Raw in
  `data/humaneval-glm51-results.json`. (Earlier 90.9% GLM-5 figure
  from project memory predates the recorded raw data.)
- Run: `chimera eval --benchmark human-eval --dataset path/to/humaneval.json`.

### Aider Polyglot

Multi-language coding benchmark from
`github.com/Aider-AI/polyglot-benchmark`. Drawn from Exercism exercises
across six target languages: Python, JavaScript, Rust, Go, Java, C++.
Each task ships a stub plus a read-only test file; the agent fills in
the stub and is graded by either expected-file diff-match or by running
the language's test command and checking the exit code.

This adapter is the **general** flavour usable by every Chimera CLI
(otter / weasel / shrew / mink / ferret). The shrew flavour at
`chimera/shrew/benchmarks/aider_polyglot.py` is a thin subclass that
exposes a small-model-friendly default language subset
(`SHREW_DEFAULT_LANGUAGES = python, javascript, rust, go`) — Java and
C++ need toolchains that aren't always installed on a small-model
laptop.

- File: `chimera/eval/benchmarks/aider_polyglot.py`
- Tests: `tests/eval/benchmarks/test_aider_polyglot.py` (26 unit tests
  covering ABC conformance, dataset loading, single + multi-language
  filters, env-var override, diff-match scorer, test-command scorer).
- Dataset: not vendored — licenses are mixed. Stage locally under
  `~/.chimera/datasets/aider-polyglot/` or override with
  `CHIMERA_AIDER_POLYGLOT_PATH=/abs/path`.
- Constructor: `AiderPolyglot(dataset_path=None, limit=None,
  languages=None, language=None)`. The list-form `languages=[...]`
  filter wins when both are supplied; the single-form `language="..."`
  is preserved for back-compat.
- Run (loader only):
  ```python
  from chimera.eval.benchmarks import AiderPolyglot

  bench = AiderPolyglot(languages=["python", "rust"], limit=10)
  for task in bench.tasks():
      ...  # drive the agent; bench.evaluate(task, output, env) -> bool
  ```

#### Setup

```bash
# 1. Clone upstream (read-only — we never pip install it):
git clone https://github.com/Aider-AI/polyglot-benchmark /tmp/polyglot

# 2. Stage tasks.json under the default dataset dir:
mkdir -p ~/.chimera/datasets/aider-polyglot
# (author tasks.json from the upstream tree — see the adapter docstring
#  for the per-task schema)

# 3. Smoke-run via shrew:
chimera shrew bench aider-polyglot --bench-limit 5 --language python
```

#### Schema

Per task in `tasks.json`:

```json
{
  "id": "python/hello-world",
  "language": "python",
  "prompt": "Implement hello().",
  "expected_files": {"hello_world.py": "def hello():\n    return 'Hello, World!'\n"},
  "test_command": "pytest -x -q",
  "exercise_dir": "hello-world",
  "timeout_s": 90
}
```

Either `expected_files` or `test_command` (or both) must be present.
When both are set, diff-match is tried first; the test command is the
fallback. The `exercise_dir` is the subdir under
`<dataset_root>/exercises/` to use as the cwd for `test_command`.

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
