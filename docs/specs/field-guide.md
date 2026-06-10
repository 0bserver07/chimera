# Field Guide to Coding Agents

**Date:** 2026-05-28
**Status:** Proposal
**Layer:** Documentation (Starlight site under `site/src/content/docs/field-guide/`)
**Team roles:** `researcher` (per-agent source reading), `executor` (page writing), `reviewer` (accuracy audit), `planner` (taxonomy structure)
**Depends on:** existing replicated-agent primitives in Chimera (≥8 confirmed; verify the full count against the codebase)
**Unblocks:** Chimera's positioning as the canonical reference for coding-agent architecture

## Problem

The coding-agent field has ~50 agents, ~50 LLMs oriented around coding, and 100+ benchmarks — and no rigorous catalog of what each agent's *architecture* actually is. Practitioners pick agents by leaderboard score, not by understanding what loop, tool set, context strategy, or termination heuristic they actually use. The information exists scattered across papers, blog posts, READMEs, and source repos; nobody has assembled it. This is the foundational artifact Chimera's comparative-methodology mission rests on.

## What This Enables

- A reader can answer "how does Aider's diff-based edit format differ from SWE-Agent's bash-as-primary-tool philosophy?" without reading either source.
- Chimera becomes the canonical reference for the coding-agent field — visited even by readers not using the framework.
- Compounding asset: each new agent added to Chimera's replica set adds one Field Guide page; the cross-cutting taxonomy improves with every entry.
- Positioning artifact: "I read the Field Guide" becomes the proxy for "I understand the space."

## Design Sketch

### Page Template (one per replicated agent)

```markdown
---
title: "{{Agent Name}}"
description: "{{One-line architectural summary}}"
---

**Origin:** {{paper / repo / company / year}}
**Loop type:** {{ReAct / Plan+Execute / Reflexion / ToT / hybrid}}
**Primary surface:** {{interactive REPL / autonomous SWE / IDE-embedded / CLI / web}}
**Chimera primitive:** the agent's codename package under `chimera/` or its style in `chimera/agents/presets/agent_styles.py` (verified path)

## Loop

{{Mermaid diagram of the agent's state machine}}

## Tool Set

| Tool          | Purpose                  | Notable Constraint             |
|---------------|--------------------------|--------------------------------|
| ...           | ...                      | ...                            |

## Prompt Strategy

- System prompt structure (sections, role)
- Use of examples / few-shot
- Edit format (diff / whole-file / search-replace / AST-patch)

## Context Strategy

- What stays in context across turns
- Compaction trigger (token threshold / manual / never)
- File tracking and re-injection behavior

## Termination Heuristic

- When does the agent stop?
- "Task complete" signal vs budget exhaustion vs human handoff

## Notable Quirks

- Anything surprising about the design
- Trade-offs the original authors made and why

## References

- Paper: ...
- Source repo: ...
- Replicated in Chimera at commit: {{git-sha}}
```

### Taxonomy Page (`field-guide/index.mdx`)

A sortable cross-cutting table:

| Agent | Loop type | Edit format | Tool budget | Context strategy | Primary surface | Year |
|-------|-----------|-------------|-------------|------------------|-----------------|------|

Plus a "reading order" recommendation: start with SWE-Agent (the bash-first archetype), then Aider (the diff-format archetype), then OpenHands (the multi-modal archetype), etc.

### Site Integration

- Lives in Starlight at `site/src/content/docs/field-guide/`.
- `field-guide/index.mdx` — taxonomy + reading guide.
- `field-guide/{{slug}}.mdx` — one page per replicated agent.
- Sidebar group: "Field Guide" between "Quickstart" and "Architecture".

## File Layout

- `site/src/content/docs/field-guide/index.mdx`
- `site/src/content/docs/field-guide/swe-agent.mdx`
- `site/src/content/docs/field-guide/aider.mdx`
- `site/src/content/docs/field-guide/cline.mdx`
- `site/src/content/docs/field-guide/codex-cli.mdx`
- `site/src/content/docs/field-guide/openhands.mdx`
- `site/src/content/docs/field-guide/gemini-cli.mdx`
- `site/src/content/docs/field-guide/opencode.mdx`
- `site/src/content/docs/field-guide/kimi-cli.mdx`
- Additional pages as new replicas land (confirm headcount against the codebase before publishing).

## Acceptance Criteria

- [ ] Headcount of replicated agents confirmed against the codename packages under `chimera/` and `chimera/agents/presets/` (do not trust prior notes).
- [ ] Index page with taxonomy table covering ≥8 agents.
- [ ] One detailed page per agent: loop diagram + tool set + prompt + context + termination + references.
- [ ] Each page links to (a) the original paper/repo and (b) the Chimera primitive replicating it (with verified file path and current commit SHA).
- [ ] Sidebar visible on the docs site under "Field Guide".
- [ ] No agent listed without firsthand source reading. **No LLM-summary stub pages.**
- [ ] Taxonomy page renders the cross-cutting comparison table in sortable form.

## Research Protocol (per agent)

For each agent, the `researcher` role must:

1. Read the Chimera replica end-to-end (the codename package under `chimera/` and any associated files).
2. Read the original source repo (typically GitHub) — at minimum the entry point, the loop, and the prompt template.
3. Read the original paper if any.
4. Write the page from this firsthand reading.
5. Cross-check the description against the replicated primitive's actual runtime behavior (run a smoke task; assert the loop matches the prose).

**Never write a page from memory or LLM summary.** Pages without primary-source citations are rejected at review.

## Open Questions

- Whether to publish loop diagrams as Mermaid (renderable inline) or as committed SVG (no JS dependency). Initial choice: Mermaid; fall back to SVG if Starlight rendering proves brittle.
- Whether the Field Guide should mention agents Chimera has not replicated yet (e.g. as "Not yet replicated" stubs). Initial choice: no — replicated only, to avoid drift.
- Whether to include benchmark scores per agent. Initial choice: no — leave scores to the comparative matrix output of [comparative-bench-cli](comparative-bench-cli.md). Field Guide is architecture, not leaderboard.

## Out of Scope

- A "Benchmark Guide" doc (separate concern, possibly later).
- Auto-generated pages or LLM-summary stubs.
- Pages for non-replicated agents.
- Per-agent benchmark scoreboards (the comparative matrix carries that).

## References

- Mission: see `README.md` and `docs/philosophy.md` — the artifact this spec produces is the foundation of replication-as-decomposition.
- Replicated agents: the codename packages under `chimera/` (verify the list against the current codebase before publishing).
