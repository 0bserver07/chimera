# Agent Presets

`chimera.agents.presets.agent_styles` provides named presets that recreate
the architecture of well-known coding agents by composing the right tools,
loop type, and system prompt from Chimera's layered stack.

## Key Classes

| Class | Description |
|-------|-------------|
| `AgentPreset` | Named configuration that builds a fully-wired `Agent` via `build()` |

## Available Presets

| Preset | Loop | Tools | Style |
|--------|------|-------|-------|
| `AgentPreset.SWE_AGENT` | `RetryLoop` (3 retries) | Minimal: read, edit, bash, search, list | Benchmark-focused, methodical |
| `AgentPreset.CODEX` | `ReAct` (50 steps) | `AGENT_TOOLS` (full set) | Memory-aware, full access |
| `AgentPreset.AIDER` | `LintFeedbackLoop` (ruff) | Edit-focused + git, test, repo_map | Pair-programming, lint-aware |
| `AgentPreset.CLINE` | `PlanActLoop` (8 plan steps) | `AGENT_TOOLS` (full set) | IDE-like, plan then execute |

## Quick Start

```python
from chimera.agents.presets.agent_styles import AgentPreset

agent = AgentPreset.SWE_AGENT.build(provider)
result = agent.run("Fix the failing test in test_utils.py", env=env)
```

## Choosing a Preset

- **SWE_AGENT** -- Best for benchmarks and well-defined bug fixes. Retries
  with scoring ensure convergence.
- **CODEX** -- General-purpose with full tool access. Good default for
  open-ended tasks.
- **AIDER** -- Best for edit-heavy workflows. Automatic lint checking catches
  style issues after each turn.
- **CLINE** -- Best for complex tasks that benefit from exploration before
  action. Read-only planning prevents accidental mutations.

## Custom Presets

Create your own preset by instantiating `AgentPreset`:

```python
my_preset = AgentPreset(
    name="my_agent",
    description="Custom agent with retry and lint.",
    tool_names=["read_file", "edit_file", "bash", "test"],
    loop_type="retry",
    loop_kwargs={"max_retries": 5},
    max_steps=40,
    system_prompt="You are a test-driven developer. Always run tests first.",
)

agent = my_preset.build(provider)
```

## Supported Loop Types

`loop_type` can be one of: `"react"`, `"retry"`, `"plan_act"`, `"lint_feedback"`.

## Import Reference

```python
from chimera.agents.presets.agent_styles import AgentPreset
```

## Related

- [Agent Config](agents-config.md) -- markdown-based agent configuration
- [Retry Loop](retry-loop.md) -- retry wrapper with scoring
- [Plan/Act Loop](plan-act-loop.md) -- two-phase plan then execute
- [Lint Feedback Loop](lint-feedback-loop.md) -- linter-driven iteration
