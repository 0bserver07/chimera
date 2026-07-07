---
title: "The Coding Agent"
description: "chimera code — the fully-assembled daily-driver coding agent, its 24 tools, loop postures, and measured benchmark results."
---

`chimera code` is the assembled coding agent: the daily driver that wires every
layer of the stack into one product. Where the tutorials build a bare agent from
four primitives, this one ships with 24 tools, a layered system prompt that reads
your live git state, conversation memory across turns, and a choice of working
postures. It is the same agent the benchmark harness measures, so the numbers
below are the numbers you run.

The class behind it is `CodingAgent` in `chimera/assembly/coding_agent.py`.

---

## Run it

```bash
pip install chimera-run[anthropic]
export ANTHROPIC_API_KEY="sk-ant-..."
```

Start the REPL in your project:

```bash
chimera code --model glm-5.2 --workdir ./my-project
```

Or the full-screen TUI, which adds a live transcript, tool log, and per-turn
telemetry:

```bash
chimera code --tui --model glm-5.2
```

Type naturally at the `>` prompt. The agent selects tools on its own, streams its
reasoning, and prints cost and step count after each turn. For the slash-command
reference (`/cost`, `/compact`, `/checkpoint`, `/tree`, and the rest), see
[Use the REPL](/chimera/guides/use-the-repl/).

---

## What's inside: 24 tools, not 4

A bare loop from the tutorials carries the four `DEFAULT_TOOLS` — read, write,
bash, and image-read. The assembled agent carries **24**, built by the `coding`
tool set and confirmed by introspection:

```python
from chimera.eval.runners.registry import coding_agent_preset_agent
# builds the same CodingAgent(preset="coding_agent") the matrix measures
```

| Group | Tools |
|-------|-------|
| **Read & search** | `read_file`, `list_files`, `search` |
| **Write & edit** | `write_file`, `edit_file`, `replace_in_file`, `apply_patch` |
| **Execute** | `bash`, `test`, `git` |
| **Web** | `web_fetch`, `web_search` |
| **Plan & track** | `think`, `todo`, `task_list`, `enter_plan_mode`, `exit_plan_mode` |
| **Delegate** | `agent`, `skill`, `task_output`, `task_stop`, `tool_search` |
| **Ask** | `ask_user` |
| **Batch** | `batch` |

That is the full set: `read_file`, `write_file`, `edit_file`, `replace_in_file`,
`apply_patch`, `list_files`, `search`, `bash`, `test`, `git`, `web_fetch`,
`web_search`, `think`, `todo`, `task_list`, `enter_plan_mode`, `exit_plan_mode`,
`agent`, `skill`, `task_output`, `task_stop`, `tool_search`, `ask_user`, and
`batch`.

The delegation tools are what let it act as a team of one: `agent` spawns an
isolated sub-agent with its own context, `skill` forks a discovered skill,
`tool_search` widens the toolset on demand, and `enter_plan_mode` / `exit_plan_mode`
gate destructive work behind an explicit plan.

---

## How it thinks

### Layered system prompt

Every turn, a `ContextAssembler` rebuilds the prompt from cacheable and live
layers: a base instruction block, tool descriptions, environment details, project
context, and — refreshed each turn — your **live git status** (current branch,
short working-tree status, recent commits). Project memory is injected as its own
non-cacheable layer on top. The agent starts each turn knowing what branch you are
on and what you have already changed.

### Conversation memory

`CodingAgent` persists its message history across `run()` calls, so a REPL or TUI
keeps full context between turns without you re-stating anything. A steering queue
lets you inject a mid-turn correction that is delivered between tool calls rather
than after the turn ends.

### Nudges

Autonomous "take an action" and "keep going" nudges keep non-interactive runs
(benchmarks, `-p` one-shots) from stalling. Interactive front-ends turn them off
(`enable_nudges=False`) so conversational Q&A does not ramble.

---

## Loop postures

A posture is a system-prompt augmentation that changes how the agent approaches a
task without swapping the reasoning loop. Two ship in `LOOP_POSTURES`:

| Posture | Behaviour |
|---------|-----------|
| `plan` | Write a short numbered plan (2–4 steps) before editing, then carry it out and revise if it proves wrong. |
| `tdd` | Work test-first: write a failing test that captures the goal, make it pass with the smallest change, run the tests before finishing. |

Postures are per-lane, so a multiplexer cohort can race plan-first against
test-first on the same model and preset.

---

## Presets

The default preset is `coding_agent` — 100 turns, permissions on, auto-compaction
on. `CodingAgent.from_preset(...)` swaps the whole posture:

| Preset | Tool set | Turns | Notes |
|--------|----------|-------|-------|
| `coding_agent` | coding (24) | 100 | The daily driver. Permissions + compaction on. |
| `codex` | coding (24) | 50 | Full-tools, action-oriented. |
| `kimi` | coding (24) | 50 | Action-first variant. |
| `swebench` | coding (24) | 30 | Tuned for SWE-bench tasks. |
| `explore` | explore | 30 | Read-only investigation. |
| `minimal` | minimal | 20 | Smallest viable toolset. |

---

## Measured strength

These are measured results, not estimates.

**Breadth — the 91-cell grid.** In the committed matrix
`data/matrix-full-glm52.json` (model `glm-5.2[1m]`, 13 agents × 7 benchmarks at
n=1 per cell under a shared budget), the `coding-agent` preset **solved all 7 of
its benchmark columns** — human-eval, human-eval-plus, mbpp-sanitized, mbpp-plus,
livecodebench-codegeneration, math500, and tau-bench:airline.

**Depth — LiveCodeBench at n=25.** In the deeper run
`data/depth-lcb-coding-agent-glm52.json` (same model), the `coding-agent` preset
scored **84% on LiveCodeBench code-generation — 21 of 25 tasks passed**, the best
of any agent on that benchmark.

---

## Next steps

- [Use the REPL](/chimera/guides/use-the-repl/) — slash commands, checkpoints, and session branching.
- [Agent × Benchmark Matrix](/chimera/guides/agent-benchmark-matrix/) — run this agent, or any other, against any benchmark.
- [Configure Permissions](/chimera/guides/configure-permissions/) — control what the agent may do.
