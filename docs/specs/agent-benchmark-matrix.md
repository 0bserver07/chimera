# Agent × Benchmark Matrix — Many-to-Many Runner Unification

**Date:** 2026-07-02
**Status:** Proposal
**Layer:** 5 (Evaluation) + 8 (CLI), over 4 (Agent) and 3 (Provider)
**Team roles:** `planner` (axis scoping + phasing), `executor` (runner protocol + registry + CLI), `reviewer` (controlled-variable correctness — same grader/sandbox/budget per cell), `researcher` (validate each external runner against one real task before it enters the registry)
**Depends on:** existing `ComparativeEval` (`chimera/eval/comparative.py`), `Harness` (`chimera/eval/harness.py`), `CodingAgentAdapter` (`chimera/eval/coding_agent_adapter.py`), `ACPClient` (`chimera/acp/client.py`), the `teammate_runner` CLI-template pattern (`chimera/mcp_servers/teammate_runner.py`); [comparative-bench-cli](comparative-bench-cli.md), [harbor-task-adapter](harbor-task-adapter.md), [atif-trajectory-emission](atif-trajectory-emission.md)
**Unblocks:** the headline mission deliverable — a published N-agents × M-benchmarks matrix under controlled variables, including *external* agents Chimera does not own

## Problem

Chimera's mission is the controlled comparative matrix for coding agents. Today the two halves of that matrix exist but do not meet:

- **Benchmark axis is broad.** ~28 `Benchmark` adapters behind one `Harness`. Solid.
- **Agent axis is narrow at the seam.** `Harness` accepts `agent: Any` and calls `agent.run(prompt, env) -> AgentResult`, but the only things wired to that contract as first-class citizens are the core ReAct `Agent` and the assembled `CodingAgent` (via `CodingAgentAdapter`). `ComparativeEval.add_config(name, agent_factory)` crosses *Chimera-internal* loops against a single task; it has no notion of an external agent, and no notion of more than one benchmark.

Meanwhile Chimera *already drives external agents* — just not through the harness:

- `chimera/acp/client.py` (`ACPClient` + `ACPSessionConfig(command=[...])`) speaks Agent Client Protocol to a subprocess. `ExternalAgentTool` wraps that as a *tool*, not as an *agent under test*.
- `chimera/mcp_servers/teammate_runner.py` drives external CLIs against a *team queue* via command templates — its committed docstring literally runs `codex exec --prompt-file {prompt_file}`, `opencode run "{prompt}"`, and `opencode acp`.

So the capability to run an external agent is present; what is missing is the **one adapter contract + registry** that lets *any* agent — a Chimera loop, an ACP subprocess, a templated CLI, or a third-party framework's own SWE-bench harness — be dropped into the same matrix cell, under the same budget, the same sandbox, and the same grader. Without that unification, "many agents integrate to many benches" is true in the architecture and false at the command line.

This spec closes that gap. It does **not** reimplement external agents. Per the mission (interop, not compete), it *drives* them as controlled variables.

## What This Enables

- `chimera bench matrix --agents A,B,C --benchmarks X,Y --model glm-5 --max-tool-calls N` → an N×M grid of `pass_rate × cost × wall_clock × tool_calls`, one ATIF v1.7 trajectory per cell.
- The agent set `A,B,C` may freely mix **internal** (`chimera-code`, `react`, `plan-execute`) and **external** (`codex`, `opencode`, `mini-swe-agent`, `agentless`, `aider`, `openhands`) entries — all enumerated from one declarative registry.
- Every cell runs the *same* task, in the *same* sandbox class, under the *same* budget, graded by the *same* grader — the only free variable is the agent. That is the "controlled comparison" the field cannot otherwise produce.
- External frameworks that ship their own SWE-bench harness (their `predictions.jsonl`) are consumed without Chimera re-driving their loop, then graded by the official SWE-bench harness so numbers are leaderboard-comparable.

## The Two Axes (verified inventory)

### Axis A — Agents (the *runner*: attempts a task, yields a result + trajectory)

| Category | How it runs | Already in repo? |
|---|---|---|
| A1 — Chimera-internal | in-process `agent.run()`; the base ReAct `Agent` + assembled `CodingAgent`, **7 codename postures**, **6 assembly presets**, **4 replica styles**, × loop postures + strategy loops (enumerated below) | ✅ `CodingAgentAdapter`, `ComparativeEval` |
| A2 — External via ACP | spawn an ACP server subprocess, send one task, read the result | ✅ `ACPClient`, `ACPSessionConfig` (used only as a *tool* today) |
| A3 — External via CLI template | subprocess with `{prompt_file}` / `{repo}` / `{patch_out}` placeholders | ✅ pattern in `teammate_runner` (`codex exec`, `opencode run`) |
| A4 — External via native harness | run the framework's *own* SWE-bench harness, collect its `predictions.jsonl`, grade with our/official grader | ❌ new (`NativeHarnessRunner`) |

#### Internal agent roster (A1, verified in-repo) — every one of these is a matrix row

The internal agent axis is already large before a single external agent is added. All resolve to an `agent_factory(provider) -> agent` and drop into `InProcessRunner`:

- **7 codename postures** (`chimera/{mink,otter,ferret,weasel,shrew,stoat,badger}/`): TUI-first · server/multi-client · sandbox/IDE · minimal-4-mode · small-local-model · shell-toggle · harness-rewrite+parity.
- **6 assembly presets** (`chimera/assembly/presets.py`): `coding_agent` (alias `claude_code`), `codex`, `minimal`, `explore`, `kimi`, `swebench` (bench-tuned: minimal edits, root-cause, no compaction/streaming). They differ by tool_set × permissions × max_turns.
- **4 replica styles** (`chimera/agents/presets/agent_styles.py`) — distinct *loops*, not just prompts: `swe_agent` (retry loop, max_retries=3) · `codex` (react) · `aider` (lint_feedback loop, ruff, max_lint_rounds=2) · `cline` (plan_act, plan_steps=8).
- **Orthogonal loop axis**: `LOOP_POSTURES` (`plan`, `tdd`) as prompt postures, plus the strategy loops via `loop_adapter` (plan-execute / reflexion / tot).

So `InProcessRunner` alone yields on the order of *(codenames ∪ presets ∪ styles) × loop-postures* distinct rows — the matrix has plenty to compare before external agents even enter.

### Axis B — Benchmarks (the *task source + grader*)

| Category | Status |
|---|---|
| B1 — Chimera-native adapters | ✅ ~28 `Benchmark` subclasses |
| B2 — Harbor-format benches | ⏳ [harbor-task-adapter](harbor-task-adapter.md) (one adapter unlocks the format) |
| B3 — External benches needing an adapter | mixed: SWE-bench + Verified + Multi-SWE-bench present; SWE-bench Full partial |

The keystone work is entirely on **Axis A**: a single runner protocol that makes A1–A4 interchangeable. Axis B is already the pluggable half.

## Design Sketch

### 1. `AgentRunner` protocol + `AgentRunResult`

The universal contract. Everything an agent produces that the matrix needs, normalized:

```python
# chimera/eval/runners/base.py
@dataclass
class AgentRunResult:
    """Normalized output of one agent attempt at one task."""
    patch: str | None = None            # unified diff, for SWE-style tasks
    answer: str = ""                    # free text, for QA / codegen tasks
    trajectory: dict | None = None      # ATIF v1.7 (see atif-trajectory-emission)
    cost_usd: float = 0.0
    tool_calls: int = 0                 # normalized budget unit (see comparative-bench-cli)
    llm_calls: int = 0
    wall_clock_sec: float = 0.0
    status: str = "completed"           # completed | budget_exhausted | error | timeout
    raw: dict = field(default_factory=dict)  # runner-specific extras

class AgentRunner(Protocol):
    """Anything that can attempt a benchmark task under a budget."""
    id: str
    def run(self, task: "BenchTask", env: "Environment", budget: "BudgetSpec") -> AgentRunResult: ...
```

`Harness` already calls `agent.run(prompt, env) -> AgentResult`; a thin shim exposes an `AgentRunner` *as* a harness `agent` so **the existing 28 benchmarks drive external agents with zero benchmark changes**. `AgentRunResult.patch`/`.answer` map onto the current `AgentResult`.

### 2. Four runner implementations (reuse what exists)

```python
# A1 — wraps the existing in-process adapters. CodingAgentAdapter refactored to satisfy AgentRunner.
class InProcessRunner(AgentRunner):
    def __init__(self, id, agent_factory): ...   # agent_factory(provider) -> agent  (ComparativeEval's contract)

# A2 — reuses chimera/acp/client.py, unchanged protocol layer.
class ACPRunner(AgentRunner):
    def __init__(self, id, config: ACPSessionConfig): ...   # e.g. command=["opencode", "acp"]

# A3 — reuses the teammate_runner subprocess/template pattern, generalized off the team queue.
class CliTemplateRunner(AgentRunner):
    def __init__(self, id, cmd_template: str, parse: Callable[[CompletedProcess], AgentRunResult]): ...
    # cmd_template placeholders: {prompt_file} {repo} {patch_out} {task_id}

# A4 — run the framework's own SWE-bench harness once, then map predictions.jsonl -> AgentRunResult per task.
class NativeHarnessRunner(AgentRunner):
    def __init__(self, id, harness_cmd: str, predictions_glob: str): ...
```

`CliTemplateRunner` and `ACPRunner` are ~90% present already — this spec lifts them out of `teammate_runner`/`ExternalAgentTool` into reusable runners. Only `NativeHarnessRunner` is greenfield.

### 3. `AgentSpec` + unified registry (the control surface)

One declarative file enumerates every agent — internal or external — so `--agents a,b,c` resolves uniformly. This *is* the "control variables" enabler:

```yaml
# ~/.chimera/agents/matrix.yaml  (project > user > built-in, like AgentLoader)
- id: chimera-code                 # A1
  kind: in-process
  factory: chimera.eval.coding_agent_adapter:CodingAgentAdapter
- id: plan-execute                 # A1
  kind: in-process
  factory: chimera.assembly.loop_adapter:plan_execute_factory
- id: opencode                     # A2
  kind: acp
  command: ["opencode", "acp"]
  sandbox: docker
- id: codex                        # A3
  kind: cli-template
  cmd: "codex exec --prompt-file {prompt_file} --cd {repo}"
  patch_from: git-diff             # collect diff from {repo} after exit
  sandbox: docker
- id: mini-swe-agent               # A4
  kind: native-harness
  harness_cmd: "python -m minisweagent.run --subset {subset} --output {out_dir}"
  predictions_glob: "{out_dir}/preds.jsonl"
  sandbox: docker
```

`AgentRegistry` (`chimera/agents/registry.py`) gains a loader for these `AgentSpec` entries alongside the existing `AgentConfig` presets.

### 4. `chimera bench matrix` — the 2D generalization of `bench-compare`

`bench-compare` (`chimera/eval/comparative.py` → `ComparativeEval`) is single-benchmark, internal-agent, one budget. This spec generalizes both axes:

```
chimera bench matrix \
  --agents chimera-code,codex,opencode,mini-swe-agent \
  --benchmarks swe-bench-lite,humaneval \
  --model glm-5 \
  --max-tool-calls 40 --max-cost-usd 0.50 \
  --sandbox docker \
  --output matrix/ --emit-atif
```

- Reuses the `BudgetSpec` from [comparative-bench-cli](comparative-bench-cli.md) — **tool calls are the normalized unit** for internal agents; for external agents that do not route through `tool_executor.py`, the budget degrades gracefully to `max_wall_clock_sec` + `max_cost_usd` (documented per cell, never silently — a runner that can only honor wall-clock/cost is flagged in the report so the "controlled" claim stays honest).
- Emits one `MatrixReport` (a grid of the existing `CompareReport` cells): `pass_rate × cost × wall_clock × tool_calls`, per `(agent, benchmark)`.
- One ATIF v1.7 trajectory per cell → Pier viewer + Chimera analyzers, uniformly.

### 5. Sandbox unification (Chimera's SWE-ReX)

External agents must not run on the host. The existing `chimera/env/` layer — `docker`, `modal_sandbox`, `e2b`, `native_sandbox`, `ssh`, `cloud`, `remote` — is the controlled execution substrate; `--sandbox docker` selects it via the existing `env_factory`. The sandbox class is itself a controlled variable: every agent in a matrix run gets the *same* env class. (A `SweRexEnvironment` wrapper is a possible future addition if we want that specific sandbox, but it is not required — our env layer already covers the role.)

### 6. Controlled-variable guarantees (what makes the matrix defensible)

- **Same grader per benchmark column.** Grading uses the benchmark's own `evaluate()` (native adapters) or the official SWE-bench harness (A4 predictions) — chosen per *column*, identical across all agent rows. The `graders=` hook on `Harness` already supports post-hoc grading.
- **Same task pool per column.** One `BenchTask` list, fanned to every runner.
- **Same budget object per run**, with per-cell honesty flags where a runner can only honor a subset.
- **Same sandbox class per run.**

## Mapping the curated list into the two axes

The list is a mix of benchmarks, agent scaffolds, production CLIs, and training/exec infrastructure. Categorized against this design. **"Replica?"** flags projects Chimera already mirrors internally (A1) — those become replica-vs-real pairs (see next section).

**Benchmarks (Axis B):**

| Project | Status | Adapter | GitHub |
|---|---|---|---|
| SWE-bench (+ experiments, sb-cli) | ✅ present | `SWEBench` / `SWEBenchVerified` | SWE-bench/SWE-bench |
| SWE-bench Pro | ❌ new adapter | B3 | scaleapi/SWE-bench_Pro-os |
| Multi-SWE-bench | ✅ present | `MultiSWEBench` | multi-swe-bench/multi-swe-bench |

**Agent scaffolds & production CLIs (Axis A — drive, don't rebuild):**

| Project | Runner | Replica? | GitHub |
|---|---|---|---|
| SWE-agent | `NativeHarnessRunner` (A4) | ✅ `swe_agent` style | SWE-agent/SWE-agent |
| mini-SWE-agent | `NativeHarnessRunner` — **first external** | — | SWE-agent/mini-swe-agent |
| OpenHands (+ software-agent-sdk) | `CliTemplateRunner`/native (A3/A4) | — | OpenHands/OpenHands |
| Agentless | `NativeHarnessRunner` — **cost baseline (~$0.34/issue)** | — | OpenAutoCoder/Agentless |
| AutoCodeRover (+ sonar-foundation-agent) | `NativeHarnessRunner` (A4) | — | AutoCodeRoverSG/auto-code-rover |
| Moatless Tools (MCTS variant) | `NativeHarnessRunner` (A4) | — | aorwall/moatless-tools |
| Open SWE (LangChain) | `CliTemplateRunner`/native | — | langchain-ai/open-swe |
| Aider | `CliTemplateRunner` (A3) + harness (A4) | ✅ `aider` style (lint_feedback) | Aider-AI/aider |
| Codex CLI | `CliTemplateRunner` (A3, `codex exec` — precedent) | ✅ `codex` style + preset | openai/codex |
| Cline | `CliTemplateRunner` (A3) | ✅ `cline` style (plan_act) | cline/cline |
| Claude Code | `CliTemplateRunner` (A3) | ≈ `coding_agent`/`claude_code` preset | anthropics/claude-code |
| Gemini CLI | `CliTemplateRunner` (A3) | — | google-gemini/gemini-cli |
| Goose (Block) | `CliTemplateRunner`/ACP (A3/A2) | — | block/goose |
| Trae Agent (ByteDance) | `NativeHarnessRunner`/A3 | — | bytedance/trae-agent |
| Refact.ai Agent | `CliTemplateRunner` (A3) | — | smallcloudai/refact |
| opencode | `ACPRunner` (A2, `opencode acp` — precedent) | — | (driven today by `teammate_runner`) |

**Training environments & exec infra (interop, mostly out of the matrix's critical path):**

| Project | Role here | GitHub |
|---|---|---|
| SWE-ReX | exec substrate → maps to `env/` layer; optional `SweRexEnvironment` | SWE-agent/SWE-ReX |
| R2E-Gym (home of DeepSWE) | task source / training env → feed Axis B via [harbor-task-adapter](harbor-task-adapter.md) | R2E-Gym/R2E-Gym |
| SWE-Gym | task source / training env (2,438 tasks) → Axis B feeder | SWE-Gym/SWE-Gym |
| SWE-smith | training-data + trajectory gen → **out of scope** for the matrix (training interop, note only) | SWE-bench/SWE-smith |
| SWE-bench/experiments | trajectory sharing → **ATIF publish sink** | via [atif-trajectory-emission](atif-trajectory-emission.md) |

Two external agents already have a driving precedent in `teammate_runner`: **codex** (A3, `codex exec`) and **opencode** (A2, `opencode acp`) — the natural Phase-1 proof cells.

## Signature experiment: replica vs. real

This is the payoff of the "replicate agents" pillar, and no single-agent project can produce it. For **swe_agent, codex, aider, cline** (and, loosely, `coding_agent` ≈ Claude Code), Chimera holds *both*:

- an **internal replica** — a real, code-backed loop (`agent_styles.py`: retry / react / lint_feedback / plan_act; assembly presets `codex`/`kimi`), and
- the ability to **drive the real external CLI** (Axis A3/A4: `openai/codex`, `Aider-AI/aider`, `cline/cline`, `SWE-agent/SWE-agent`).

Running the pair `(replica_X, real_X)` on the same benchmark, same model, same budget, same sandbox measures **replica fidelity**: `|pass_rate(replica) − pass_rate(real)|` plus trajectory-shape divergence (step count, tool mix, edit locality). That is exactly what **badger's parity-tracking posture** exists to score — this spec gives it a benchmark to score against.

**Deliverable:** a *fidelity table* — one row per replicated agent, replica-vs-real Δpass-rate and Δcost on a fixed bench. It turns "we replicated agent X" from a claim into a measured number, and it is the most defensible artifact the comparative-methodology framing can ship.

## Phasing (0.9.x discipline — a plan, not a release)

- **Phase 0 — protocol + registry + matrix CLI over internal agents.** `AgentRunner`, `AgentRunResult`, `InProcessRunner` (refactor `CodingAgentAdapter` under it), `AgentSpec` loader, `chimera bench matrix` crossing internal agents × the full native bench registry. **Enumerate the internal roster** (7 codenames + 6 presets + 4 styles) as `matrix.yaml` entries — nearly free, and it yields the first real internal-only matrix on day one. Zero external dependencies; ships the keystone.
- **Phase 1 — ACP + CLI-template runners.** Lift `ACPRunner`/`CliTemplateRunner` out of `teammate_runner`/`ExternalAgentTool`. Prove one external agent (start with `codex` or `opencode` — already driven) on one existing bench (HumanEval or SWE-bench Lite) end-to-end, in `docker`.
- **Phase 2 — `NativeHarnessRunner` + the SWE-bench agent fleet.** Add registry entries (not framework code) for mini-SWE-agent (recommended start), Agentless (cost baseline), Aider, OpenHands, AutoCodeRover, Moatless, Open SWE. Grade via the official SWE-bench harness for column-consistency. Wiring the real Aider/Cline/Codex/SWE-agent CLIs here **unlocks their replica-vs-real pairs** against the Phase-0 internal styles.
- **Phase 3 — external benchmark axis + first published matrices.** Fill Axis-B gaps (SWE-bench Pro; SWE-bench Full; Harbor / R2E-Gym / SWE-Gym task sources via [harbor-task-adapter](harbor-task-adapter.md)), then publish (a) a real ~4-agents × ~3-benches matrix on GLM-5 and (b) the **replica-vs-real fidelity table** for `swe_agent`/`codex`/`aider`/`cline` — ATIF trajectories opened in Pier.

## Non-goals / honest framing

- **We do not reimplement external agents.** We drive them (ACP / CLI / native harness) as controlled variables. Their scores are *theirs*, reproduced under *our* controls.
- **Budget parity is best-effort for external agents.** Only agents routing through `tool_executor.py` honor `max_tool_calls` exactly. External runners honor wall-clock + cost and are flagged in the report — no silent "controlled" claim where control is partial.
- **Spec ≠ done.** This file is a plan with zero code. Nothing here ships until built and verified on a real model (GLM-5), not mocks.
- **Numbers here are illustrative.** No pass-rate is claimed until a matrix actually runs.

## Trademark hygiene

External benchmark and agent-framework names in this spec (SWE-bench, mini-SWE-agent, OpenHands, Aider, Agentless, AutoCodeRover, Refact-bench, SWE-ReX, codex, opencode, Harbor, Pier) are named as **third-party interop targets** — the same convention already used by `chimera/mcp_servers/teammate_runner.py` (which drives `codex`/`opencode` by name) and by the eval benchmark adapters (SWE-bench, Aider-Polyglot, GAIA, WebArena). This spec introduces no codename↔upstream-brand identity claim, so the per-codename `scripts/*_trademark_scrub.sh` checks (mink/otter/ferret/weasel/shrew/stoat/badger) are unaffected. New runner code must live under `chimera/eval/runners/` (not a codename subtree) to keep it that way.

## Open questions

1. **Prompt contract for external agents.** SWE-bench-style tasks give a repo + issue; codegen tasks give a function stub. Does each `AgentSpec` declare its input shape (`repo+issue` vs `prompt`), or does the runner negotiate from the `BenchTask` type? (Leaning: `BenchTask` carries a discriminated `kind`; runners declare which kinds they accept and are skipped — logged — for others.)
2. **Patch extraction for A3.** `git-diff` after exit vs an explicit `{patch_out}` the CLI must write. Support both via `patch_from: git-diff | file`.
3. **Cost accounting for external agents** that use their own keys/providers — parse from their logs where possible, else mark cost `unknown` (never fabricate).
4. **Concurrency.** External harnesses are heavy; cap parallel cells per `--sandbox` class.

## Acceptance criteria

- `AgentRunner` + `AgentRunResult` land with `InProcessRunner` passing the existing `Harness` contract (no benchmark changes).
- At least 3 internal roster entries (mixing codenames / presets / styles) resolve from `matrix.yaml` and run as distinct rows.
- `chimera bench matrix` produces a 2×2 grid (2 internal agents × 2 native benches) on GLM-5 with per-cell ATIF trajectories.
- One external agent (`codex` or `opencode`) resolves from `matrix.yaml` and completes one SWE-bench-Lite task in `docker`, graded identically to the internal agents.
- One replica-vs-real pair (e.g. the `aider` style vs the real Aider CLI) produces a fidelity Δpass-rate on a fixed bench.
- `MatrixReport` flags any cell whose budget was honored only partially.
- `bash scripts/all_trademark_scrub.sh` stays green.
