---
title: The coding-agents map
description: How Chimera's coding agents fit together — CLIs, presets, models, and the multiplexer — and when to use which.
---

Chimera exposes more than one way to run a coding agent, and the names overlap
enough to be confusing the first time. This page is the map. The trick is to see
that a run is built from **four independent knobs** — pick one value on each and
you have a fully specified agent.

## The four knobs

| Knob | Flag | What it selects | Example values |
|------|------|-----------------|----------------|
| **1. Which CLI** | the subcommand | the agent *application* — its chrome, defaults, transports | `code` (default) + `mink` `otter` `ferret` `weasel` `shrew` `stoat` `badger` |
| **2. Which preset** | `--preset` | a *configuration* of the agent — tool set + system prompt | `coding_agent` · `codex` · `minimal` · `explore` |
| **3. Which model** | `--model` / `--models` | the *LLM* doing the work | `glm-5.2` · `glm-4.6` · `kimi-k2` · … |
| **4. How many at once** | `--tui --models a,b,c` | one agent, or the multiplexer | 1 = single pane · N = race |

They are orthogonal: any CLI can run any preset on any model, alone or in a
cohort. Most confusion comes from treating two knobs as one.

## Knob 1 — which CLI

`chimera code` is the general-purpose default. Beside it are **seven codename
CLIs**, each modelled on a different *style* of upstream coding agent and tuned
to a different posture — same library substrate underneath (one agent loop, one
tool registry, one session store), different opinions on top:

| CLI | Alias | Posture |
|-----|-------|---------|
| `mink` | `tui` | TUI-first, keyboard-heavy interactive coding |
| `otter` | `multi` | server-first / multi-client (CLI · REPL · HTTP+SSE · ACP) |
| `ferret` | `sandbox` | sandbox-first, IDE-flagship, single-flag approval presets |
| `weasel` | `mini` | minimal harness, four operating modes, tiny surface |
| `shrew` | `tiny` | tuned for small local models, restricted tool set |
| `stoat` | `shell` | shell-mode toggle — one buffer for commands and questions |
| `badger` | `strict` | tight step budget, rerun-on-failure, parity tracking |

Pick the CLI whose posture matches your workflow. The full comparison lives in
the [Coding Agents Overview](/chimera/concepts/agents/) and the
[Inspirations page](/chimera/inspirations/). For everyday use, `chimera code` is
the one to reach for.

## Knob 2 — which preset

A preset configures the agent stack that `chimera code` (and the multiplexer)
run. It changes the **tool set** and the **system prompt**, not the model:

| Preset | Shape |
|--------|-------|
| `coding_agent` | the full daily-driver: read/write/edit/bash/search/test/git + the sharpened coding prompt (default) |
| `codex` | a leaner, patch-oriented posture |
| `minimal` | a stripped tool set for cheap or constrained runs |
| `explore` | read-and-understand tools, tuned for "explain this repo" |

Select one with `--preset`:

```bash
chimera code --preset explore        # understand a codebase, no edits
```

## Knob 3 — which model (`--model` vs `--models`)

This is the pair that trips people up:

- **`--model glm-5.2`** — singular. The one model for a single-agent run.
- **`--models glm-5.2,glm-4.6`** — plural, comma-separated. The *list* of models,
  one per lane, for the multiplexer.

In `--tui` mode, a non-empty `--models` wins: one entry runs the single-agent
TUI, two or more launch the multiplexer. Each entry may pin a preset with
`model:preset`, so you can compare configurations too:

```bash
chimera code --tui --models glm-5.2,glm-5.2:codex,glm-4.6
#                            └ model  └ model:preset  └ model
```

## Knob 4 — one agent, or the multiplexer

- **One model** (`--tui`, or `--tui --models glm-5.2`) → the single-agent TUI:
  a full-screen transcript, live streaming, mid-run steering.
- **Two or more** (`--tui --models a,b,c`) → the **multiplexer**: N lanes racing
  the *same task*, side by side, each in its own isolated workspace.

The comparison-oriented alias `chimera otter --multiplex a,b,c` launches the
exact same multiplexer — it is a second door to the same code, not a different
agent per lane.

## Why race models — the point of the multiplexer

Chimera exists for **controlled comparison**: hold everything constant (task,
tools, isolation) and vary only the model, so differences are attributable. That
answers questions you otherwise guess at:

- Is the cheaper/faster model good enough here, or do I need the strong one?
- Which model actually produced the better fix for *this* task?

You watch cost, tokens, steps, time, and outcome diverge live, then compare what
each model *wrote* — not vibes, evidence. No single-session coding agent does
this; it is Chimera's differentiator.

## Where your data goes

Persistence depends on knob 1:

| Surface | Location | What's stored |
|---------|----------|---------------|
| `chimera code` REPL | `~/.chimera/sessions/*.jsonl` | resumable session tree (fork / branch / resume) |
| the 7 codename CLIs | `~/.chimera/eventlog/<agent>-<utc>-<uuid>/` | event-sourced run log |
| **the multiplexer** | `~/.chimera/cohorts/<id>/` | the cohort comparison artifact |

A cohort directory is self-contained and is the multiplexer's payoff:

```
~/.chimera/cohorts/<id>/
├── manifest.json          # the shared task + each lane's model/preset/workspace/base-commit
├── summary.json           # the ranked scoreboard (who won, cost, tokens, steps, time)
├── lane-A.transcript.txt  # every message lane A produced
├── lane-A.diff            # the code lane A actually wrote (from its isolated worktree)
├── lane-B.transcript.txt
└── lane-B.diff
```

Those `lane-*.diff` files are the comparison: the same task, and exactly what
each model produced for it. `--export run.zip` bundles the whole cohort into a
portable archive. A cohort is also **resumable** — `chimera code --tui --resume
<id>` (list ids with `--list-cohorts`) reopens the lanes with their history and
produced changes restored, and continues the race.

## Which do I use?

- **Just code with an agent** → `chimera code` (add `--tui` for the full screen).
- **Understand a repo first** → `chimera code --preset explore`.
- **Decide between two models for a task** → `chimera code --tui --models a,b`.
- **Compare a model against itself under two presets** → `--models m:coding_agent,m:codex`.
- **A different posture** (server / sandbox / small-model / shell) → the matching
  [codename CLI](/chimera/concepts/agents/).

## See also

- [Building a TUI or REPL on Chimera](https://github.com/0bserver07/chimera/blob/master/docs/building-a-tui.md) — the driver contract and the multiplexer modules.
- The `interactive-frontends` spec (`docs/specs/interactive-frontends.md`) — the
  single-agent TUI and multiplexer, phase by phase.
- [Coding Agents Overview](/chimera/concepts/agents/) — the seven CLIs in depth.
