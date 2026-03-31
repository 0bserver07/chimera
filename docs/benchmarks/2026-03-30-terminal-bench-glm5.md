# Terminal-Bench — GLM-5.0 Report

**Date:** 2026-03-20 (original run), 2026-03-30 (this report)
**Model:** GLM-5.0 via api.z.ai
**Result:** 3/10 passed (30%)
**GLM-5.1 run:** Not completed — Terminal-Bench dataset download infrastructure was broken on 2026-03-30.
**Raw data:** Not saved (original run predates data/ directory)

## What Terminal-Bench Measures

Terminal-Bench tests whether an agent can complete CLI tasks in a Linux terminal. Each task:
- Runs in a Docker container with a tmux session
- Agent receives a natural language instruction (e.g., "set up a cron job that logs disk usage every hour")
- Agent sends bash commands one at a time to the tmux session
- Agent reads terminal output after each command
- Terminal-Bench's verifier checks if the task was completed

This is an **agent benchmark** with a different action space than SWE-bench: instead of file tools, the agent operates through a terminal session.

## Exact Setup

### Environment

```bash
export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
export ANTHROPIC_API_KEY="<key>"
export ANTHROPIC_MODEL="glm-5"  # GLM-5.0 for the original run
```

### Dependencies

```bash
pip install terminal-bench
# Requires: Docker, tmux
```

### Method

Chimera provides a Terminal-Bench agent adapter at `chimera/benchmarks/terminal_bench_agent.py`. The adapter:

1. Receives the task instruction from Terminal-Bench
2. Sends it to the LLM with a system prompt: "You are an expert terminal operator..."
3. LLM returns a bash command
4. Adapter sends the command to tmux via `session.send_keys()`
5. Waits 2 seconds, reads terminal output via `session.get_visible_pane_content()`
6. Feeds output back to LLM as the next turn
7. Repeats until LLM says "DONE" or 30 turns reached
8. Terminal-Bench's verifier checks if the task was completed

### Parameters

| Parameter | Value | Justification |
|-----------|-------|---------------|
| `max_turns` | 30 | Enough for most terminal tasks |
| Output wait | 2 seconds | Fixed sleep after each command |
| System prompt | "Expert terminal operator. Return ONLY the command. DONE when finished." | Keeps output clean |
| Agent loop | **Custom** (not Chimera's ReAct) | Direct provider.complete() in a loop — Terminal-Bench controls the session, not Chimera's loop |
| Tools | **None** (bash only via tmux) | Terminal-Bench's paradigm — everything through the terminal |
| Temperature | 0.0 | Greedy decoding |

### Important Note on Architecture

The Terminal-Bench adapter does **not** use Chimera's agent loop or tools. It's a thin wrapper:
- Chimera provides the Provider (LLM connection)
- Terminal-Bench provides the execution environment (Docker + tmux)
- The adapter bridges them with a simple send-command/read-output loop

This means Chimera's tools (file read/write/edit), strategies (ReAct, PlanAndExecute), and infrastructure (permissions, events, detection) are **not used**. The 30% score reflects the LLM's raw terminal operation ability through a minimal adapter.

### Runner Command

```bash
tb run \
  --agent-import-path "chimera.benchmarks.terminal_bench_agent:ChimeraAgent" \
  --model "anthropic/glm-5" \
  --dataset terminal-bench-core \
  --n-tasks 10 \
  --n-concurrent 1
```

## Results (GLM-5.0)

**3/10 passed (30%)**

Per-task results were not saved from the original run. The 30% figure and 10-task count are from the benchmark transparency framework (`docs/benchmarks/README.md`).

## Why 30%?

| Factor | Impact |
|--------|--------|
| **2-second fixed wait** | Too short for slow commands (package installs), too long for fast ones. Should be adaptive. |
| **No error recovery** | If a command fails, the agent doesn't retry or try an alternative approach. |
| **Minimal system prompt** | No guidance on common patterns (systemctl, cron syntax, file permissions). |
| **Terminal-only action space** | Agent can't read files directly — must `cat` them through the terminal. |
| **Model capability** | GLM-5.0 is not as strong at terminal operations as Claude or GPT-4. |

### Comparison with State of the Art

| System | Terminal-Bench Score |
|--------|---------------------|
| **Chimera (GLM-5.0)** | **30%** |
| Claude Code | ~45% |
| Terminus (specialized) | ~56% |

## GLM-5.1 Run — Why It Failed

On 2026-03-30, we attempted to run Terminal-Bench with GLM-5.1:

```bash
tb run \
  --agent-import-path "chimera.benchmarks.terminal_bench_agent:ChimeraAgent" \
  --model "anthropic/glm-5.1" \
  --dataset terminal-bench-core \
  --n-tasks 10
```

This failed with:
```
FileNotFoundError: [Errno 2] No such file or directory:
'/var/folders/.../tmpXXXXXXXX/tasks'
```

The error is in Terminal-Bench's dataset download code (`terminal_bench/registry/client.py:279`), not in Chimera. The TB registry failed to download and extract the dataset. This is an infrastructure issue on Terminal-Bench's side.

## Improvements to Make Before Next Run

| Fix | Expected impact | Effort |
|-----|-----------------|--------|
| Adaptive wait (poll for prompt instead of fixed 2s) | Avoids timeouts on slow commands | Medium |
| Error recovery (detect failed command, retry) | Agent can recover from typos/wrong commands | Medium |
| Richer system prompt (common patterns, OS conventions) | Fewer wasted turns on basic operations | Low |
| Use Chimera's ReAct loop instead of custom loop | Gets loop detection, events, step tracking | Medium |
| Cache dataset locally | Avoids TB registry failures | Low |

## Reproduction

```bash
# 1. Install dependencies
pip install terminal-bench
# Ensure Docker and tmux are installed

# 2. Set up environment
source .env

# 3. Run
tb run \
  --agent-import-path "chimera.benchmarks.terminal_bench_agent:ChimeraAgent" \
  --model "anthropic/glm-5.1" \
  --dataset terminal-bench-core \
  --n-tasks 10 \
  --n-concurrent 1 \
  --output-path runs/tb-glm51

# 4. Results in runs/tb-glm51/
```
