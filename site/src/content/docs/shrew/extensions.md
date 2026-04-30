---
title: Shrew extensions
description: The three small-model-fit extensions shrew layers on top of weasel — moe_offload, scaffold_fit, and tool_filter — what each does, when to tune it, and how to disable.
---

# Extensions

Shrew layers three small-model-fit extensions on top of
[weasel](../weasel/quickstart.md). Each extension addresses a
specific failure mode that frontier models don't have but small
local models do. They live under `chimera/shrew/extensions/` and
are wired into the loop config by `chimera shrew`'s argparse path
(`apply_small_model_extensions()` in `chimera/shrew/cli.py`).

This page covers what each extension does, when to tune it, and
how to disable it.

## Overview

| Extension | What it tunes | Default trigger |
|---|---|---|
| `moe_offload` | Context window size | All MoE-aware models, every run. |
| `scaffold_fit` | System-prompt wrapping | Active params < 13B. |
| `tool_filter` | Tool list trimming | Active params < 9B. |

The three extensions are independent and pure functions. Each
returns a new value rather than mutating its input. None of them
touch the network or the filesystem; they're all stdlib-only and
deterministic.

## moe_offload

### What it does

Picks a safe context window for the active model given the
caller's VRAM budget. The MoE-offload trick (experts in RAM,
attention on GPU) means the only GPU consumer that scales with
context is the **KV cache**. The extension computes a context
window that fits the KV cache in the remaining VRAM budget, snapped
to a power of two and clamped at the model's architectural
maximum.

The math, briefly:

```
free_gb = vram_gb - attention_vram_gb - reserve_gb
kv_budget = free_gb * GB * KV_BUDGET_FRACTION
tokens = kv_budget // kv_bytes_per_token
context = round_to_power_of_two(tokens), clamped [4096, max_context]
```

Per-model facts live in
[`MOE_MODEL_CATALOG`](https://github.com/0bserver07/chimera/blob/master/chimera/shrew/extensions/moe_offload.py)
as `MoEModelProfile` records: total params, active params,
estimated KV bytes per token, attention VRAM cost, architectural
context cap.

### When to tune

- **Bigger GPU.** Pass `--vram-gb 24` (or set `$SHREW_VRAM_GB=24`)
  to unlock 32k context windows on a workstation.
- **Tight laptop.** `--vram-gb 6` returns a smaller context
  (8k for `qwen3.6-35b-a3b`, 4k for the dense 9B).
- **New model.** Add a `MoEModelProfile` entry to the catalog with
  measured numbers. The defaults are deliberately conservative; if
  you've measured the actual KV-bytes-per-token for your model,
  drop a profile and the helper will use it.

### How to disable

There is no kill-switch for `moe_offload`. The extension is purely
a sizing helper — the worst it does is return a too-small context
window. If you want to override its decision, pin the context on
the llama.cpp side via `-c` and ignore the helper:

```bash
# llama-server side: hard-code -c 32768
# shrew side: pass --max-tokens to control output budget separately
```

The helper still runs but its output is no longer load-bearing.

## scaffold_fit

### What it does

Wraps the system prompt with a small-model reasoning scaffold for
sub-13B models. Frontier models are forgiving about open-ended
prompts ("you're a helpful coding assistant; figure it out"); small
local models do dramatically better with an explicit reasoning
frame. The wrapper layers in:

- A **preamble** ("You are running on a small local model. Think
  step-by-step before acting. Emit one tool call per turn.").
- A **reasoning checklist** (restate goal → list sub-tasks →
  pick next step → choose tool → emit call).
- An **output rules block** (one tool call per turn; don't narrate;
  prefer edit over rewrite; ask the user only when truly blocked).

The wrapper is **idempotent**: if the prompt already contains the
`<small-model-scaffold>` tag, it's returned unchanged. Safe to
apply at multiple stages.

### When to tune

The threshold lives at `SMALL_MODEL_THRESHOLD_B` (default `13.0`)
in `chimera/shrew/extensions/scaffold_fit.py`. Models with **fewer
active parameters** than this get the scaffold; everything else
passes through unchanged.

For MoE models, "active parameters" means the active-experts count
— so `qwen3.6-35b-a3b` is treated as a 3B model and gets the
scaffold even though its nominal label is 35B. This is the
behaviour you want; the model only "thinks" with 3B params at any
given step.

When to lower the threshold:

- You're running a high-quality 13-15B model that already
  reasons well. Drop the threshold to `9` or `7`.

When to raise it:

- You're running a 13B that's still rambling. Push the threshold to
  `16` or `20`. The scaffold is sometimes useful even on the
  borderline.

### How to disable

Two options:

1. Per-invocation: pass a frontier-class model. `--model claude-sonnet-4-6`
   reports a size of `None` (unknown), and `wrap_for_small_model` is
   fail-open on unknown sizes — frontier models don't get the
   scaffold imposed on them.
2. Edit the threshold in source. There is no CLI flag for this
   today; the assumption is that if you want the scaffold off, you
   want it off for everything below frontier, in which case you set
   the threshold to `0`.

A `--no-scaffold` flag is on the post-release roadmap.

## tool_filter

### What it does

Drops tools that empirically confuse sub-9B models. The deny-set
ships with nine names: `web_fetch`, `browser`, `browser_navigate`,
`browser_click`, `browser_extract`, `image_read`, `delegate`,
`import_graph`, `repo_map`. It also drops complex MCP-namespaced
tools (any tool whose name matches `mcp__.+__.+`) on the
assumption that 9B models confuse MCP tools for their built-in
counterparts.

The filter is a pure function; it returns a new tool list with the
disallowed tools removed, in original order.

### When to tune

The threshold lives at `TINY_MODEL_THRESHOLD_B` (default `9.0`) in
`chimera/shrew/extensions/tool_filter.py`. Models with **fewer
active parameters** than this get the deny-set applied; everything
else gets the full tool surface.

The deny-set itself is `TOOLS_TO_DROP_FOR_TINY` (a frozenset).
Callers using the SDK can pass `extra_drops=frozenset({"git",
"...","..."})` to merge in additional names. There is no CLI flag
for the deny-set today; the assumption is that the small-model
default is the right baseline for 99% of users.

When to lower the threshold:

- You're confident your 7-9B model can handle the full tool
  surface. Drop the threshold to `5`.

When to raise it:

- You're seeing tool-confusion errors in a 13B model. Push the
  threshold to `13` and re-run.

### How to disable

Pass `--allowed-tools=` (empty) on the CLI. That hands the agent
the full default tool group, bypassing the per-tool filter. The
trade-off is you lose the small-model default of
`Read,Write,Edit,Bash`.

For finer control, use the SDK directly and pass
`tools=filter_tools_for_model(my_tools, model_id, extra_drops=...)`
or skip the helper entirely.

## How they compose at runtime

`chimera/shrew/cli.py` calls `apply_small_model_extensions()` once
during arg parsing. The function returns a dict with:

- `model` — the resolved model id.
- `context_window` — output of `compute_optimal_context_window()`.
- `model_size_b` — best-effort active-parameter count.
- `system_prompt` — wrapped (or unchanged) prompt.
- `tools` — filtered (or unchanged) tool list.

The extensions are independent — you can use one without the
others. Embedders driving shrew via the SDK can call each
extension directly:

```python
from chimera.shrew.extensions import (
    compute_optimal_context_window,
    filter_tools_for_model,
    model_size_billions,
    wrap_for_small_model,
)

ctx = compute_optimal_context_window("qwen3.6-35b-a3b", vram_gb=8)
size = model_size_billions("qwen3.6-35b-a3b")            # 3.0 (active)
prompt = wrap_for_small_model(my_prompt, size)
tools = filter_tools_for_model(my_tools, "qwen3.6-35b-a3b")
```

All four functions are stdlib-only and pure.

## Tuning advice — when extensions help vs. hurt

| Situation | Recommendation |
|---|---|
| First time using shrew with the default model | Leave all three on; they're tuned for the default. |
| Running a frontier model via `--model` | They're fail-open; defaults are fine. |
| Benchmarking a model you've measured | Pin `--vram-gb` to your real number; consider `--no-skills` for cleaner runs. |
| Hitting tool-not-available errors | `--allowed-tools=` to disable `tool_filter`'s deny-set. |
| OOM on llama.cpp | Drop `--vram-gb` by 2 and re-launch llama.cpp with the same `-c` value. |
| Model rambles instead of acting | The scaffold should be picking it up; verify `model_size_billions(...)` returns `< 13`. |

## See also

- [`small-model-setup.md`](small-model-setup.md) — the
  `--n-cpu-moe 999 --flash-attn on` incantation `moe_offload`
  assumes you're using.
- [`skills.md`](skills.md) — `scaffold_fit` is a per-turn frame;
  skills are the recall index. Both layer onto the system prompt.
- [`parity-matrix.md`](parity-matrix.md) — extension parity status
  vs. the upstream small-model coding agent.
- Source: [`chimera/shrew/extensions/`](https://github.com/0bserver07/chimera/tree/master/chimera/shrew/extensions).
