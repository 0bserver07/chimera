# SWE-bench Lite — GLM-5.1 Report

**Date:** 2026-03-30
**Model:** GLM-5.1 via api.z.ai (Anthropic-compatible endpoint)
**Result:** 2/20 resolved (10%)
**Cost:** $1.50 total ($0.075/instance average)
**Time:** 76 minutes (20 instances)
**Raw data:** `data/swebench-lite-glm51-results.jsonl`

## What SWE-bench Measures

SWE-bench tests whether an agent can fix real GitHub issues. Each instance is:
- A real bug report from a real repository (Django, Flask, pytest, matplotlib, etc.)
- The specific commit where the bug exists
- The test(s) that should pass after the fix
- The gold-standard patch (for reference, not given to the agent)

This is an **agent benchmark**. The agent must read code, understand the problem, navigate the repo, edit files, and produce a fix that makes failing tests pass.

## Exact Setup

### Environment

```bash
# .env file
export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
export ANTHROPIC_API_KEY="<key>"
export ANTHROPIC_MODEL="glm-5.1"
```

### Dataset

300 instances from SWE-bench Lite (princeton-nlp/SWE-bench_Lite on HuggingFace). We run the 20 easiest (sorted by gold patch size — smallest patches first).

```bash
pip install datasets
python3 -c "
from datasets import load_dataset
import json
ds = load_dataset('princeton-nlp/SWE-bench_Lite', split='test')
with open('/tmp/swe-bench-lite.jsonl', 'w') as f:
    for r in ds:
        f.write(json.dumps(dict(r)) + '\n')
"
```

### Method

For each instance:

1. `git clone --depth 100` the repository (e.g., django/django)
2. `git checkout` the base commit where the bug exists
3. Create a Chimera Agent with full tool suite (read, write, edit, bash, search, list_files, test, git, etc.)
4. Give the agent: problem statement + list of failing test names
5. Agent runs a ReAct loop for up to 15 steps
6. After the agent finishes, run the originally-failing tests
7. RESOLVED if all tests pass; FAILED otherwise

### Parameters

| Parameter | Value | Justification |
|-----------|-------|---------------|
| `max_steps` | 15 | **This is the main bottleneck.** Each step = 1 LLM call. |
| Loop | ReAct (reason-act-observe) | Default loop, no planning phase |
| Tools | AGENT_TOOLS (15 tools) | read, write, edit, bash, search, list_files, test, git, etc. |
| Environment | LocalEnvironment (local filesystem) | No Docker isolation |
| System prompt | Default (no specialization) | Generic agent prompt |
| Instance selection | 20 smallest patches | Easiest-first from 300 total |
| Retries | 0 | Single attempt per instance |
| Temperature | 0.0 | Greedy decoding |

### Runner Script

```bash
source .env
python examples/swe_bench_lite_run.py --count 20 --model glm-5.1 --max-steps 15
```

The script is at `examples/swe_bench_lite_run.py` (152 lines).

## Results

**2/20 resolved (10%)**

### Per-Instance Results

| Instance | Repo | Tests | Steps | Cost | Status |
|----------|------|-------|-------|------|--------|
| django__django-12113 | django | 0/1 | 15 | $0.096 | FAILED |
| django__django-11179 | django | 0/1 | 15 | $0.083 | FAILED |
| django__django-12908 | django | 0/2 | 15 | $0.084 | FAILED |
| django__django-13230 | django | 0/1 | 15 | $0.101 | FAILED |
| django__django-13447 | django | 0/1 | 15 | $0.104 | FAILED |
| django__django-15814 | django | 0/1 | 15 | $0.078 | FAILED |
| matplotlib__matplotlib-23563 | matplotlib | 0/1 | 15 | $0.080 | FAILED |
| matplotlib__matplotlib-25433 | matplotlib | 0/1 | 15 | $0.067 | FAILED |
| pylint-dev__pylint-7080 | pylint | 0/1 | 15 | $0.050 | FAILED |
| **pytest-dev__pytest-11143** | **pytest** | **1/1** | **15** | **$0.077** | **RESOLVED** |
| **pytest-dev__pytest-6116** | **pytest** | **2/2** | **15** | **$0.093** | **RESOLVED** |
| astropy__astropy-12907 | astropy | 0/2 | 15 | $0.108 | FAILED |
| astropy__astropy-6938 | astropy | 0/2 | 15 | $0.056 | FAILED |
| django__django-10914 | django | 0/1 | 15 | $0.047 | FAILED |
| django__django-10924 | django | 0/1 | 15 | $0.057 | FAILED |
| django__django-11049 | django | 0/1 | 15 | $0.066 | FAILED |
| django__django-11133 | django | 0/1 | 15 | $0.062 | FAILED |
| django__django-12125 | django | 0/2 | 15 | $0.058 | FAILED |
| django__django-13964 | django | 0/1 | 15 | $0.056 | FAILED |
| django__django-14017 | django | 0/2 | 15 | $0.082 | FAILED |

### Key Observations

1. **Every failure used all 15 steps.** No instance failed early — the agent always ran out of budget.
2. **Both successes were pytest instances.** Pytest has a simpler, flatter codebase than Django. The agent could navigate, find, and fix within 15 steps.
3. **12/20 instances are Django.** Django is a massive codebase (500K+ lines). 15 steps is not enough to even understand the relevant module.
4. **Average cost is $0.075/instance.** The model is cheap; the issue is not cost but step budget.

### Why 10%? Root Cause Analysis

| Root cause | Impact | Evidence |
|------------|--------|----------|
| **15 steps is not enough** | Critical | Every failure = 15 steps exhausted. Agent spends 5-8 steps exploring, 3-5 steps attempting fixes, never gets to verify. |
| **No repo map injection** | High | Agent starts blind. Wastes steps running `find` and `ls` to understand structure. |
| **No test feedback loop** | High | Agent edits files but never runs the failing tests mid-loop to check progress. Tests only run AFTER the agent finishes. |
| **Generic prompt** | Medium | No repo-specific guidance. Agent doesn't know Django's module structure or testing conventions. |
| **Single attempt** | Medium | No retry strategy. If the first approach fails, the agent doesn't get a second chance. |
| **No planning phase** | Medium | ReAct jumps straight into actions. A plan-then-act approach would spend steps more wisely. |

### Comparison with State of the Art

| System | SWE-bench Lite Score | Steps | Key Differences |
|--------|---------------------|-------|-----------------|
| **Chimera (this run)** | **10%** | 15 | Naive ReAct, no repo context, no test feedback |
| OpenHands | ~50% | 100-500 | IPython + bash, LLM condensation, specialized prompts |
| Claude Code | ~65% | Unlimited | Full context management, tool streaming, recovery |
| Codex CLI | ~70% | Unlimited | Sandboxed execution, auto-test, built-in recovery |

The gap is configuration, not framework capability. Chimera has all the primitives (tools, loops, strategies, test execution) but this benchmark run used the simplest possible configuration.

## Improvements to Make Before Next Run

| Fix | Expected impact | Effort |
|-----|-----------------|--------|
| Increase `max_steps` to 50 | Agents no longer run out of budget on simple instances | Trivial — change one number |
| Inject repo map at start | Agent knows file structure before acting | Low — use RepoMap tool output as context |
| Run failing tests after each edit | Agent gets immediate feedback on progress | Low — add test command to agent prompt |
| Use PlanAndExecute loop | Plan phase (read-only) before edit phase | Low — swap loop type |
| Specialize prompt per repo | "This is a Django project, tests are in tests/, models in models/" | Medium |
| Use Reflexion strategy | Self-critique after failure, retry with lessons | Medium |
| Increase to 100 instances | More statistically meaningful results | Time only |
| Add Docker isolation | Prevent agent from corrupting shared state | Medium |

### Projected Impact

With steps=50 + repo map + test feedback + PlanAndExecute:
- Conservative estimate: **25-30%** (5-6/20)
- Optimistic estimate: **35-40%** (7-8/20)

This would not match OpenHands (50%) or Claude Code (65%) because those use hundreds of steps and specialized scaffolding. But it would demonstrate the framework works.

## Reproduction

```bash
# 1. Install dataset
pip install datasets
python3 -c "
from datasets import load_dataset; import json
ds = load_dataset('princeton-nlp/SWE-bench_Lite', split='test')
f = open('/tmp/swe-bench-lite.jsonl', 'w')
[f.write(json.dumps(dict(r)) + '\n') for r in ds]
"

# 2. Set up environment
source .env

# 3. Run benchmark (takes ~60-90 minutes for 20 instances)
python examples/swe_bench_lite_run.py --count 20 --model glm-5.1 --max-steps 15

# 4. Results printed to stdout, saved to /tmp/swebench_lite_results_glm-5.1.jsonl

# To run with more steps:
python examples/swe_bench_lite_run.py --count 20 --model glm-5.1 --max-steps 50
```
