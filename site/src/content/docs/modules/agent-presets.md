---
title: "Agent Presets"
description: "Agent Presets"
---

`chimera.agents.presets.agent_styles` provides named presets that recreate
the architecture of well-known coding agents by composing the right tools,
loop type, and system prompt from Chimera's layered stack.

> **Deprecated.** `AgentPreset.build(provider)` emits `DeprecationWarning`
> and will be removed in v0.7.0. The canonical replacement is
> `chimera.assembly.coding_agent.CodingAgent.from_preset(...)`. See the
> [migration guide](/chimera/migrations/v0.4-to-v0.5/#agentpresetbuild--codingagentfrom_preset).

## Key Classes

| Class | Description |
|-------|-------------|
| `AgentPreset` | Named configuration; legacy factory ([deprecated](/chimera/migrations/v0.4-to-v0.5/#agentpresetbuild--codingagentfrom_preset)) |
| `CodingAgent` | Canonical fully-assembled stack — use `CodingAgent.from_preset(name)` |

## Available Presets

The legacy `AgentPreset` enums and their `CodingAgent.from_preset(...)` analogues:

| Legacy preset | Canonical replacement | Loop | Style |
|---------------|-----------------------|------|-------|
| `AgentPreset.SWE_AGENT` | `CodingAgent.from_preset("swebench")` | `RetryLoop` (legacy) / `AgentLoop` (new) | Benchmark-focused, root-cause |
| `AgentPreset.CODEX`     | `CodingAgent.from_preset("codex")`    | `ReAct` (legacy) / `AgentLoop` (new) | Memory-aware, full access |
| `AgentPreset.AIDER`     | `CodingAgent.from_preset("coding_agent")` | `LintFeedbackLoop` (legacy) / `AgentLoop` (new) | Pair-programming |
| `AgentPreset.CLINE`     | `CodingAgent.from_preset("coding_agent")` | `PlanActLoop` (legacy) / `AgentLoop` (new) | IDE-like, plan first |

## Quick Start (canonical)

```python
from chimera.assembly.coding_agent import CodingAgent

agent = CodingAgent.from_preset("swebench")
async for event in agent.run("Fix the failing test in test_utils.py"):
    print(event)
```

## Choosing a Preset

- **swebench** -- Best for benchmarks and well-defined bug fixes. Minimal
  scaffold, no transcripts/compaction, focuses on root cause.
- **codex** -- General-purpose with full tool access. Good default for
  open-ended tasks. Permissions on, hooks off.
- **kimi** -- Action-first, KISS. Iterates on failures.
- **coding_agent** -- Default canonical stack. Permissions, hooks,
  transcripts, content replacement, compaction, streaming all enabled.
- **minimal** / **explore** -- Restricted toolsets for low-risk runs.

## Custom Presets

Define a custom `AssemblyConfig` and register it in `PRESETS`:

```python
from chimera.assembly.presets import AssemblyConfig, PRESETS
from chimera.assembly.coding_agent import CodingAgent

PRESETS["my_agent"] = AssemblyConfig(
    name="my_agent",
    description="Custom agent: lint-aware, transcript on, full tools.",
    tool_set="coding",
    permissions=True,
    hooks=False,
    transcripts=True,
    max_turns=40,
)

agent = CodingAgent.from_preset("my_agent")
```

## Supported Loop Types

`loop_type` can be one of: `"react"`, `"retry"`, `"plan_act"`, `"lint_feedback"`.

## Import Reference

```python
from chimera.agents.presets.agent_styles import AgentPreset
```

## Related

- [Agent Config](/agents-config/) -- markdown-based agent configuration
- [Retry Loop](/retry-loop/) -- retry wrapper with scoring
- [Plan/Act Loop](/plan-act-loop/) -- two-phase plan then execute
- [Lint Feedback Loop](/lint-feedback-loop/) -- linter-driven iteration
