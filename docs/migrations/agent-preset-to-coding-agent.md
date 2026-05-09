# Migration: `AgentPreset` -> `CodingAgent.from_preset`

**Status (v0.7.0):** `AgentPreset.build()` has been **removed**. Calling
`AgentPreset.SWE_AGENT.build(provider)` (or any sibling) now raises
`AttributeError`. This page remains as a historical migration record;
the recipe below is what you want to land on. For an in-tree benchmark
or test that absolutely needs the bare-`Agent` profile (no permissions /
hooks / transcripts / compaction / snapshots), call
`AgentPreset._compose(provider)` instead — it's the same code path the
old `build()` delegated to (private now, but still supported).

> Pre-0.7.0 readers: the entries below describe the deprecation that
> was active in v0.5 / v0.6. The migration recipe still applies.

## Why two preset systems exist

Chimera has carried two preset implementations side by side for several
releases. Understanding the split makes the migration mechanical rather than
mysterious.

### `chimera.agents.presets.agent_styles.AgentPreset` (legacy)

The original primitive. Each `AgentPreset` is a thin recipe that picks:

- a tool list (e.g. `["read_file", "edit_file", "bash"]`)
- a loop variant (`react`, `retry`, `plan_act`, `lint_feedback`)
- a system prompt and `max_steps`

`AgentPreset.build(provider)` returns a bare `chimera.core.agent.Agent`. It
does *not* wire permissions, hooks, transcripts, content replacement,
compaction integration, snapshot manager, or persistent memory. It is the
agent equivalent of "raw torch tensors" — useful for benchmarks and
single-shot evaluations, but not a product.

### `chimera.assembly.coding_agent.CodingAgent` (canonical)

The fully-assembled coding agent that ships in v0.5+. `CodingAgent` composes
all eight architecture phases — permission checking, hook executor, transcript
storage, content replacement state, compaction integration, snapshot manager,
persistent memory, slash command processor — and is what `chimera code`,
`chimera review`, and `chimera ci-fix` actually run on.

`CodingAgent.from_preset("swe_agent")` is the supported entry point.

### History

`AgentPreset` predates the assembly layer. When the assembly layer landed it
shadowed the older primitive, but the primitive stayed callable to avoid
breaking downstream notebooks and benchmarks. v0.7.0 is the cutoff: by then
the assembly path will have been the canonical surface for two minor
releases.

## Direct migration recipe

Replace each call site one-for-one. The preset names map directly.

```python
# Before
from chimera.agents.presets.agent_styles import AgentPreset
agent = AgentPreset.SWE_AGENT.build(provider)

# After
from chimera.assembly.coding_agent import CodingAgent
agent = CodingAgent.from_preset("swe_agent", provider=provider)
```

`CodingAgent` accepts `provider=` to keep your existing provider construction
intact, or `model="..."` if you want it to build a provider for you.

## Preset name map

| Legacy `AgentPreset`           | Canonical `CodingAgent.from_preset(...)` |
| ------------------------------ | ---------------------------------------- |
| `AgentPreset.SWE_AGENT.build`  | `CodingAgent.from_preset("swe_agent")`*  |
| `AgentPreset.CODEX.build`      | `CodingAgent.from_preset("codex")`       |
| `AgentPreset.AIDER.build`      | `CodingAgent.from_preset("coding_agent")`*|
| `AgentPreset.CLINE.build`      | `CodingAgent.from_preset("coding_agent")`*|

The four assembly presets that map cleanly today:

- `coding_agent` — full canonical coding agent (replaces `claude_code` alias)
- `codex` — code generation profile (Codex style, transcripts on)
- `minimal` — minimal tool set, permissions/hooks/transcripts off
- `explore` — read-only exploration agent

\* `swe_agent` is reachable via the `swebench` assembly preset
(`CodingAgent.from_preset("swebench")`) which is benchmark-tuned with
permissions, hooks, transcripts, content replacement, compaction, and
streaming all disabled — exactly the lean profile the legacy `SWE_AGENT`
provided. Aider and Cline did not have direct assembly equivalents; their
loop variants (`lint_feedback`, `plan_act`) are not yet exposed via the
assembly layer, so map them to `coding_agent` and re-run with the canonical
ReAct loop, or pin to v0.6.x if you depend on those exact loops.

## Behavior changes you should expect

- Tool set is wider by default. `CodingAgent` uses the `coding`/`minimal`/
  `explore` tool factories, which include richer tool surface than the legacy
  preset's hand-picked list.
- Permissions are on for `coding_agent` and `codex`. Pass
  `permission_callback=` or rely on the default BYPASS fallback for
  non-interactive use.
- Transcripts are written under `<project>/.chimera/sessions/`.
- Snapshot manager records modified files per turn; you can `reset_abort()`
  and inspect snapshots.

## Suppressing the warning during migration

If you genuinely need to keep the legacy call for one more release:

```python
import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    agent = AgentPreset.SWE_AGENT.build(provider)
```

This pattern keeps test output quiet while you work through the migration.
After v0.7.0 the import itself will be removed.

## Removal target

**v0.7.0 (shipped 2026-05-09).** `AgentPreset.build()` was removed in
v0.7.0; the wrapper class `AgentPreset` itself remains so the in-tree
`_compose()` escape hatch keeps working. The
`chimera.agents.presets.agent_styles` module is therefore still
importable; the only contract that broke is the public `build()`
method.
