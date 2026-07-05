---
title: The Agent × Benchmark Ecosystem — Explainer & How to Extend
description: What every agent type, preset, style, loop, runner kind, and benchmark family MEANS — and copy-pasteable steps for adding your own. The narrative companion to the capability matrix.
---

# The Agent × Benchmark Ecosystem

This page explains what the pieces of Chimera's comparative matrix *mean* and how
to *add more of them*. It is the narrative companion to the
[capability matrix](./capability-matrix.md) — that page is the inventory (the
counts and the wired-vs-designed status); this page is the explainer and the
extension guide. If you want "how many agents are there," read the matrix. If you
want "what is a replica style, and how do I add one," read this.

Everything below was read out of the source; where a count could be read two ways
(e.g. "26 registered" vs "29 adapters"), the two scopes are spelled out so nothing
is overstated.

---

## 1. The big picture: one Harness, two axes

Chimera's mission is a *controlled comparative matrix* for coding agents. The
whole design reduces to one idea:

> **One agent-agnostic `Harness` drives any agent against any benchmark. Agents
> are one axis, benchmarks are the other, and every cell of the grid holds the
> same task, sandbox, budget, and grader — so the agent is the only free
> variable.**

The seam that makes this work is a single contract. The `Harness`
(`chimera/eval/harness.py`) has always called
`agent.run(prompt, env) -> AgentResult`. That is fine for a Chimera agent running
in-process, but it says nothing about an *external* agent — an ACP subprocess, a
third-party CLI, or a framework that ships its own SWE-bench harness. So the
matrix widens the contract to **`AgentRunner`**, and normalizes every agent's
output into **`AgentRunResult`**:

```python
# chimera/eval/runners/base.py
class AgentRunner(Protocol):
    id: str                                   # this agent's row label in the matrix
    def run(self, task, env=None, budget=None) -> AgentRunResult: ...
```

`AgentRunResult` is the one row-shape the matrix aggregates — a defined,
JSON-friendly record so a ReAct loop, an `opencode` subprocess, and a native
`predictions.jsonl` all report the same fields:

| Field | Meaning |
|---|---|
| `patch` | Unified diff, for SWE-style repo-fix tasks (`None` if the agent returned prose). |
| `answer` | Free-text answer, for code-gen / QA tasks. |
| `trajectory` | ATIF v1.7 trajectory dict when emitted, else `None`. |
| `cost_usd` | Dollar cost of the attempt. |
| `tool_calls` | Tool-call count — the normalized budget unit for agents that route through `tool_executor.py`. |
| `llm_calls` | API-turn count. |
| `wall_clock_sec` | Wall-clock duration. |
| `status` | `completed` \| `budget_exhausted` \| `error` \| `timeout`. |
| `raw` | Runner-specific extras (native result, stderr, exit code, …). |

Two CLIs sit on top of this contract:

- **`chimera bench-matrix`** — N agents × M benchmarks, one budget/sandbox/grader
  per run. Ships and is live-verified on `glm-5.2[1m]`.
- **`chimera bench-fidelity`** — a replica vs. the real external agent it mirrors,
  scored on the same benchmarks. Ships.

Both resolve their `--agents` from the same declarative registry described next.

---

## 2. Agent types & runner kinds — the 4 KINDS

Every agent — Chimera's own or someone else's — enters the matrix as one
**`AgentSpec`**, a flat JSON-serializable record. The spec's `kind` field decides
which runner brings it to life. There are exactly **4 kinds** (the `VALID_KINDS`
tuple in `chimera/eval/runners/registry.py`):

| Kind | What it means | When to use it | Key spec fields | Concrete example |
|---|---|---|---|---|
| **`in-process`** | A Chimera agent built by an `agent_factory(provider) -> agent` and wrapped in `InProcessRunner`. Runs inside the Python process. | Any internal loop / preset / style. The only kind live-verified end-to-end today. | `factory: "module:callable"` | `react` → `chimera.eval.runners.registry:react_agent` |
| **`acp`** | An **Agent Client Protocol** subprocess. Chimera spawns it, sends one task, reads the result over JSON-RPC. | An external agent that speaks ACP (e.g. `opencode acp`). | `command: ["opencode", "acp"]` | `opencode` in the external example registry |
| **`cli-template`** | A templated CLI invocation. Chimera fills placeholders (`{prompt_file}`, `{repo}`, `{patch_out}`) and shells out. | An external CLI that takes a prompt and edits a repo. | `cmd: "codex exec --prompt-file {prompt_file} --cd {repo}"` | `codex-cli`, `aider-cli` |
| **`native-harness`** | Run a framework's *own* SWE-bench harness once, then grade the `predictions.jsonl` it emits — Chimera never re-drives its loop. | A framework that ships its own end-to-end harness and you want leaderboard-comparable numbers. | `harness_cmd` + `predictions_glob` | `mini-swe-agent`, `agentless` |

`resolve(spec, provider)` maps each kind to its runner: `InProcessRunner`,
`ACPRunner`, `CliTemplateRunner`, or `NativeHarnessRunner`. **Status honesty:**
all four runner classes are *shipped code*. Only `in-process` has been
live-verified end-to-end (`react × human-eval → 100%` on `glm-5.2[1m]`). The
three external kinds are built and importable, but running them needs the
external CLI installed and (for `native-harness`) live infra plus the official
grader — that is a *deployment* gap, not a code gap. See the
[tasks backlog](../specs/agent-benchmark-matrix.tasks.md).

> **Why external kinds don't take `model` or `sandbox` in their constructors:**
> those are *matrix-level* controlled variables. The sandbox is applied uniformly
> via the run's `env_factory`, and an external agent uses its own model/keys. The
> spec carries `sandbox`/`model` for documentation and in-process use; external
> runners read them at the matrix layer, not the agent layer.

---

## 3. The internal roster layers

"How many internal agents are there" has no single answer because the internal
axis is **layered**, and the layers compose. Chimera ships a *representative*
12-agent default roster (`default_agent_specs()`), drawn from three of these
layers. Here is what distinguishes each layer:

| Layer | What it is | Count | In the 12-agent default roster? | Source |
|---|---|---:|---|---|
| **Loops** | The reason/act control flow (ReAct, plan-then-execute, reflexion, …). The lowest-level behavior primitive. | 8 | 4 of them (`react`, `plan-execute`, `reflexion`, `tree-of-thought`) | `chimera/core/loops/` + `chimera/core/loop.py` |
| **Assembly presets** | A full agent *configuration* — which tool set, whether permissions/hooks/compaction/streaming are on, the turn budget. Toggles capability, not reasoning shape. | 6 | 5 of them (`full-tools`, `action-first`, `minimal`, `explore`, `swebench`; `full-tools`/`action-first` are the `codex`/`kimi` presets exposed under loop-descriptive ids) | `chimera/assembly/presets.py` |
| **Loop styles** | In-tree loop-posture styles, each pinning a *distinct loop* + tool set + prompt. Named for the loop, not the external agent whose shape they echo. | 4 | 3 of them (`retry-min`, `lint-loop`, `plan-act`) | `chimera/agents/presets/agent_styles.py` |
| **Codename CLIs** | 7 shipped daily-driver CLIs, each a *posture* over the assembled stack (`mink`, `otter`, … `badger`). | 7 | Not by default — add via a JSON registry file | `chimera/{codename}/` |
| **External** | Agents Chimera does *not* own, driven via `acp` / `cli-template` / `native-harness`. | open | Not by default — add via a JSON registry file | `docs/examples/agent-registry.example.json` |

**The 12-agent default roster** = 4 loops + 5 presets + 3 styles:

```
react · plan-execute · reflexion · tree-of-thought       (4 loop postures)
full-tools · action-first · minimal · explore · swebench (5 assembly presets)
retry-min · lint-loop · plan-act                         (3 loop styles)
```

The roster ids are loop-descriptive. The former brand-named ids (`swe-agent` /
`aider` / `cline` / `codex` / `kimi`) still resolve as back-compat aliases —
`--agents aider` is identical to `--agents lint-loop`.

Two deliberate design choices worth knowing:

- **The `react-full` *style* is intentionally omitted** from the roster — the
  assembly `codex` preset (roster id `full-tools`) already occupies that
  full-tools/ReAct slot. So the 4 styles contribute only 3 rows. (Keeping both
  the internal `full-tools` replica and a `codex-cli` external entry distinct is
  what makes the replica-vs-real fidelity comparison possible.)
- **The roster is representative, not exhaustive.** The 7 codenames, the 4
  subagent profiles, the composition patterns, and the synthesis strategies (all
  enumerated in the [capability matrix](./capability-matrix.md)) are *not* in the
  built-in roster. They enter the matrix by shipping additional JSON registry
  files that `load_registry` merges on top of the built-ins — exactly the
  extension path in §9.

---

## 4. Presets — a full agent configuration

An **assembly preset** is a named `AssemblyConfig`: it selects a tool set and
flips the capability switches (permissions, hooks, transcripts, content
replacement, compaction, streaming) plus a `max_turns` budget. It does **not**
change the reasoning loop — that is the styles' job. Presets answer *"how much
machinery does this agent get?"*

| Preset | Optimizes for | Tool set | Perms | Hooks | Compaction / Streaming | max_turns |
|---|---|---|:--:|:--:|:--:|--:|
| `coding_agent` | Daily-driver full capability (the canonical preset; `claude_code` is a deprecated alias) | coding | ✅ | ✅ | ✅ / ✅ | 100 |
| `codex` | Focused code generation | coding | ✅ | ❌ | ✅ / ✅ | 50 |
| `kimi` | Action-first, KISS, iterate on failures | coding | ✅ | ❌ | ✅ / ✅ | 50 |
| `minimal` | Cheap, small tasks with basic tools | minimal | ❌ | ❌ | ✅ / ✅ | 20 |
| `explore` | Read-only codebase understanding | explore | ❌ | ❌ | ✅ / ✅ | 30 |
| `swebench` | Benchmark determinism — minimal edits, root-cause focus | coding | ❌ | ❌ | ❌ / ❌ | 30 |

`swebench` is the only preset that turns *off* content replacement, compaction,
and streaming — those are sources of run-to-run variance that a benchmark wants
gone. Five of the six are in the default roster: `codex`/`kimi` under the
loop-descriptive roster ids `full-tools`/`action-first`, plus `minimal`,
`explore`, `swebench`. (The preset *keys* in `presets.py` stay `codex`/`kimi`;
only the roster ids are renamed.) `coding_agent` is the interactive default and
is added to a matrix by name when you want it.

---

## 5. Styles — a replica with its own loop

A **loop style** (`AgentPreset` in
`chimera/agents/presets/agent_styles.py`) pins a **distinct loop**, a
specific tool set, and a matching system prompt. This is the layer that makes
"this control flow is code-backed" a real claim rather than a prompt tweak —
each style runs a genuinely different loop. The names are **loop-descriptive**;
the "echoes" column below notes the external agent whose shape each one mirrors
(for the replica-vs-real fidelity comparison), but that agent is not what the
style *is*.

| Style | Loop | Echoes (fidelity target) | Tools | max_steps |
|---|---|---|---|--:|
| `retry-min` | `retry` (max_retries=3) | swe-agent-style | minimal: read / edit / bash / search / list_files | 30 |
| `react-full` | `react` | codex-cli-style | full `AGENT_TOOLS` | 50 |
| `lint-loop` | `lint_feedback` (ruff, max_lint_rounds=2) | aider-cli-style | git-aware: +git / test / repo_map | 20 |
| `plan-act` | `plan_act` (plan_steps=8) | cline-style | full `AGENT_TOOLS` | 25 |

The style is `_compose(provider)`'d into a runnable `Agent` that already
satisfies the `run(prompt, env)` factory contract. In the default roster, the
`retry-min`/`lint-loop`/`plan-act` styles map to ids of the same name; the
`react-full` style is dropped in favor of the `full-tools` preset (the `codex`
preset, see §3). The former attribute names (`SWE_AGENT` / `CODEX` / `AIDER` /
`CLINE`) and roster ids (`swe-agent` / `aider` / `cline`) remain as back-compat
aliases.

---

## 6. Loops — the reasoning control flow

A **loop** is the reason→act→observe cycle itself — the most fundamental behavior
primitive. There are **8** loop implementations (`chimera/core/loops/`, with
`react` re-exported from `chimera/core/loop.py`):

| Loop | One-liner |
|---|---|
| `react` | Reason → Act (tool call) → Observe (result) → repeat, until no tool calls remain or `max_steps` is hit. |
| `retry` | Wrap any inner loop with retry + scoring. |
| `plan_act` | Two-phase: a read-only planning phase, then full execution. |
| `plan_execute` | Two-phase: ask the LLM for a plan, then execute it step by step. |
| `reflexion` | Act → Reflect → Repeat — self-critique between attempts. |
| `tree_of_thought` | Explore multiple reasoning branches (simplified ToT). |
| `lint_feedback` | Run a linter after edits and feed the errors back in. |
| `autonomous` | Long-running loop with goal decomposition and replanning. |

**Which loops are wired where:** the 4 in `default_agent_specs()` (`react`,
`plan-execute`, `reflexion`, `tree-of-thought`) come from the `_LOOP_PATHS` map,
reused verbatim from `bench_compare.py` so the roster stays a single source of
truth. The other 4 are not standalone roster entries but are *used*: `retry`,
`plan_act`, and `lint_feedback` back the `retry-min`, `plan-act`, and `lint-loop`
styles respectively; `autonomous` is available for direct use.

---

## 7. Benchmarks — the families

The other axis is task sources + graders. Every benchmark is a `Benchmark`
subclass (`name()` / `tasks()` / `evaluate()`) behind the one `Harness`.

**Count, precisely:** the shared CLI registry `_BENCHMARKS`
(`chimera/cli/main.py`) registers **26 distinct** benchmarks (44 keys once
hyphen/no-hyphen aliases are counted). These are the ones `chimera bench` and
`chimera bench-matrix` can reach. The [capability matrix](./capability-matrix.md)
reports **29 adapters** repo-wide — the extra 3 are the "shrew-side" benches
(GAIA, Terminal-Bench, HarborBench) surfaced through `chimera shrew bench`, not
the shared `_BENCHMARKS` registry. Both numbers are correct for their scope; the
matrix CLIs operate over the **26**.

The 26 registered, by family (this breakdown sums to 26):

| Family | Count | Members |
|---|---:|---|
| SWE / repo-fix | 11 | `swe-bench` · `senior-swe-bench` · `swe-bench-verified` · `multi-swe-bench` · `swe-polybench` · `swe-lancer` · `swt-bench` · `feature-bench` · `cline-bench` · `dpai-arena` · `harbor` |
| Code-gen | 8 | `human-eval` · `humaneval-plus` · `humaneval-x` · `mbpp` · `bigcodebench` · `livecodebench` · `programbench` · `aider-polyglot` |
| Math | 2 | `aimo` · `math-500` |
| Agentic / web | 2 | `tau-bench` · `webarena` |
| Long-context | 2 | `nocha` · `context-bench` |
| Generic harness | 1 | `custom` |

### Wired vs. needs-dataset vs. designed

Three different senses of "ready" — keep them distinct:

- **Wired** — reachable and instantiable through `_load_benchmark`. **All 26**
  are wired. `_load_benchmark` is signature-aware: it inspects each constructor
  and passes the dataset argument under whatever name that adapter declares
  (`dataset_path` / `problems_path` / `dataset_dir`) and only passes `limit` when
  accepted. So every adapter loads regardless of its individual signature.
  Two grading caveats within "wired": **swe-lancer** loads but `evaluate()`
  raises `NotImplementedError` (needs the upstream containerized Playwright
  harness), and **livecodebench** grades only its `codegeneration` scenario.
- **Needs a staged dataset** — "wired" ≠ "has a published result." Most adapters
  deliberately refuse to vendor upstream data (multi-GB payloads, upstream
  licenses) and expect a locally-staged `--dataset PATH`. For the publicly
  redistributable ones, **`chimera bench-fetch <name>` (or `--all`) stages the
  dataset once** into `~/.chimera/datasets/` and `_load_benchmark`
  auto-discovers it — after fetching, the bench runs with no flag at all
  (mbpp 427 tasks, humaneval-plus 164, swe-bench Lite 300, livecodebench 175
  — the newest release_v6 slice, public-test grading — all live-verified).
  Gated or license-unclear datasets stay manual. See the
  [benchmarks README](../benchmarks/README.md) for per-bench run status.
- **Designed / needs-live-infra** — enumerated in the
  [tasks backlog](../specs/agent-benchmark-matrix.tasks.md), *not* a code gap:
  running the external native-harness fleet, the official SWE-bench grader,
  SWE-bench Pro/Full adapters, R2E-Gym / SWE-Gym task feeders, and the first
  published multi-cell matrices. These wait on live external infrastructure.

Reusable across every benchmark: the 6 graders (`chimera/eval/graders/`) and the
sandbox layer (`chimera/env/`) — enumerated in the capability matrix.

---

## 8. Integrations — how an external agent plugs in

External agents enter through the same registry as internal ones, via one of the
three external kinds. The worked example lives in
`docs/examples/agent-registry.example.json` — five entries, one per
driving mode:

```json
[
  { "id": "opencode",      "kind": "acp",
    "command": ["opencode", "acp"], "sandbox": "docker" },

  { "id": "codex-cli",     "kind": "cli-template",
    "cmd": "codex exec --prompt-file {prompt_file} --cd {repo}",
    "sandbox": "docker", "options": {"patch_from": "git-diff"} },

  { "id": "aider-cli",     "kind": "cli-template",
    "cmd": "aider --yes --message-file {prompt_file} {repo}", "sandbox": "docker" },

  { "id": "mini-swe-agent", "kind": "native-harness",
    "harness_cmd": "python -m minisweagent.run --subset {subset} --output {out_dir}",
    "predictions_glob": "{out_dir}/preds.jsonl", "sandbox": "docker" },

  { "id": "agentless",     "kind": "native-harness",
    "harness_cmd": "python agentless/run.py --output {out_dir}",
    "predictions_glob": "{out_dir}/all_preds.jsonl", "sandbox": "docker" }
]
```

Reading each mode off the example:

- **`acp` (`opencode`)** — Chimera spawns `opencode acp`, opens an ACP session,
  sends the task, reads the structured result. Use when the agent speaks ACP.
- **`cli-template` (`codex-cli`, `aider-cli`)** — Chimera writes the prompt to a
  file, substitutes `{prompt_file}` / `{repo}`, runs the command, and collects
  the patch. `options.patch_from: "git-diff"` tells the runner to take the diff
  of `{repo}` after exit (vs. an explicit `{patch_out}` file). Use for any CLI
  that takes a prompt and edits a repo.
- **`native-harness` (`mini-swe-agent`, `agentless`)** — Chimera runs the
  framework's own harness once and grades the `predictions.jsonl` it drops at
  `predictions_glob`. Use when you want the framework's *own* end-to-end numbers,
  graded consistently per column.

This is **interop, not compete**: Chimera drives these agents as controlled
variables; their scores are theirs, reproduced under Chimera's budget/sandbox/
grader. The `sandbox: "docker"` on every entry is the shared controlled variable
— no external agent runs on the host.

---

## 9. How to extend (the important part)

Three recipes. Each is additive — you never edit existing agents or benches.

### A. Add a benchmark

1. **Write a `Benchmark` subclass** under `chimera/eval/benchmarks/`. Implement
   the three abstract methods (`name`, `tasks`, `evaluate`):

   ```python
   # chimera/eval/benchmarks/my_bench.py
   from __future__ import annotations
   from typing import Any
   from chimera.eval.harness import Benchmark

   class MyBench(Benchmark):
       def __init__(self, dataset_path: str | None = None, limit: int | None = None) -> None:
           self._dataset_path = dataset_path
           self._limit = limit

       def name(self) -> str:
           return "my-bench"

       def tasks(self) -> list[dict[str, Any]]:
           # each task dict needs at least "prompt"; include "id" for tracking
           return [{"id": "t1", "prompt": "…", "test": "…"}]

       def evaluate(self, task: dict[str, Any], agent_output: str, env: Any) -> bool:
           # return True iff agent_output satisfies the task
           ...
   ```

   Name your dataset constructor argument `dataset_path`, `problems_path`, or
   `dataset_dir` — the signature-aware `_load_benchmark` handles any of them.
   Accept a `limit` kwarg if the bench can be capped.

2. **Register it** in the `_BENCHMARKS` dict in `chimera/cli/main.py`
   (add hyphen and no-hyphen aliases to match the convention):

   ```python
   "my-bench": "chimera.eval.benchmarks.my_bench:MyBench",
   "mybench":  "chimera.eval.benchmarks.my_bench:MyBench",
   ```

   It is now reachable from `chimera bench`, `chimera bench-matrix`, and
   `chimera bench-fidelity` as `--benchmarks my-bench`.

### B. Add an internal agent (loop / preset / style)

1. **Write a factory** `agent_factory(provider) -> agent` in
   `chimera/eval/runners/registry.py`. It must return an object exposing
   `run(prompt, env)`. Reuse the existing helpers:

   ```python
   # a new loop posture — reuse _build_loop_agent
   def my_loop_agent(provider: Any) -> Any:
       return _build_loop_agent(provider, "chimera.core.loops.autonomous:AutonomousLoop")

   # a new preset — reuse _build_preset_agent (preset must exist in PRESETS)
   def my_preset_agent(provider: Any) -> Any:
       return _build_preset_agent(provider, "coding_agent")

   # a new loop style — reuse _build_style_agent (AgentPreset attr name)
   def my_style_agent(provider: Any) -> Any:
       return _build_style_agent(provider, "RETRY_MIN")
   ```

2. **Append an `AgentSpec`** to `default_agent_specs()`:

   ```python
   AgentSpec(id="my-loop", kind="in-process",
             factory="chimera.eval.runners.registry:my_loop_agent"),
   ```

   That id is now a matrix row: `chimera bench-matrix --agents my-loop,react …`.

   *(For a new assembly preset, add it to `PRESETS` in
   `chimera/assembly/presets.py` first; for a new style, add the `AgentPreset`
   instance in `agent_styles.py`.)*

### C. Add an external agent (no code — JSON only)

External agents need **zero framework code**. Write a JSON registry file whose
entries are `AgentSpec` dicts, then point the CLI at it with `--registry`:

```json
// my-agents.json
[
  { "id": "goose", "kind": "acp", "command": ["goose", "acp"], "sandbox": "docker" },
  { "id": "my-cli", "kind": "cli-template",
    "cmd": "mycli run --prompt {prompt_file} --repo {repo}",
    "sandbox": "docker", "options": {"patch_from": "git-diff"} }
]
```

```bash
chimera bench-matrix \
  --agents react,goose,my-cli \
  --benchmarks human-eval,swe-bench \
  --registry my-agents.json \
  --model glm-5
```

`load_registry` starts from the 12 built-ins and merges each `--registry` file in
order, so later files override earlier ones (and the built-ins) by `id`. Missing
files are skipped quietly — project- and user-level registries are optional.

### CLI reference

**`chimera bench-matrix`** — N agents × M benchmarks, one budget/sandbox/grader:

```bash
chimera bench-matrix \
  --agents react,plan-execute \        # default; registry ids, comma-separated
  --benchmarks human-eval,mbpp \       # required; registry names
  --model glm-5 \                      # shared by every cell (default: glm-5)
  --registry my-agents.json \          # optional JSON registries, merged over built-ins
  --dataset PATH --limit 20 \          # optional dataset + per-bench task cap
  --max-tool-calls 40 --max-cost 0.50 \# budget knobs (per task)
  --max-llm-calls N --max-wall-clock S \
  --format terminal|json|markdown \    # default: terminal
  --output matrix.json --env local|none
```

**`chimera bench-fidelity`** — one internal replica vs. the real external agent:

```bash
chimera bench-fidelity \
  --replica full-tools \               # required; internal replica id (the codex preset)
  --real codex-cli \                   # required; real external agent id (needs --registry)
  --benchmarks human-eval \            # required
  --registry my-agents.json \          # where the real agent's spec lives
  --model glm-5 \                      # shared by both
  --format markdown                    # default: markdown
```

Fidelity reports `|pass_rate(replica) − pass_rate(real)|` plus a coarse
trajectory-divergence proxy (tool-call-count difference), reusing the matrix
layer so every controlled-variable guarantee holds for free. It never re-drives
an agent and never fabricates a number.

**Live status:** `chimera bench-matrix --agents react --benchmarks human-eval
--model glm-5.2[1m]` has run end-to-end (100%, 1/1) via the z.ai endpoint. The
external-agent cells are shipped code awaiting the external CLIs + infra.

---

## See also

- [capability-matrix](./capability-matrix.md) — the inventory: every count and the wired-vs-designed status (this page is its explainer).
- [agent-benchmark-matrix spec](../specs/agent-benchmark-matrix.md) — the many-to-many design + the replica-vs-real signature experiment.
- [agent-benchmark-matrix tasks](../specs/agent-benchmark-matrix.tasks.md) — the phased backlog: what is shipped vs. designed vs. needs-live-infra.
- [benchmarks README](../benchmarks/README.md) — the benchmark axis, per-bench run status and methodology.
- [coding-agents](../coding-agents.md) — the 7 codename CLIs, when to pick which.
