---
title: Coding Agents Overview
description: Comparative tour of the five Chimera coding-agent CLIs — mink, otter, ferret, weasel, shrew — when to pick which, and how their provider chains differ.
---

# Coding Agents Overview

Chimera ships five coding-agent CLIs, each modeled on a different style of
upstream coding agent. They share the same library substrate — one
`AgentLoop`, one tool registry, one event-sourced session store, one set of
provider adapters — but each one has its own opinionated surface: REPL
chrome, default tool set, default model, persistence layout, and transports.
This page is the comparative map. Pick the agent whose posture matches your
workflow; pick a provider per agent; cross-link to the per-agent quickstart
when you want depth.

The family, in one sentence each:

- **mink** — TUI-first, keyboard-heavy, mirrors the upstream terminal-coding-agent UX
  most people already know.
- **otter** — server-first / multi-client, drives one ReAct loop from a CLI,
  REPL, HTTP+SSE server, or ACP transport.
- **ferret** — IDE-flagship, sandbox-first, single-flag approval presets,
  ACP-default `serve`, optional cloud bridge.
- **weasel** — minimal harness, four operating modes (interactive / print /
  RPC / SDK), almost no chrome, an auto-discovered extensions directory.
- **shrew** — small-local-model agent, layered on weasel, llama.cpp
  defaults, restricted tool set, MoE-aware context sizing, benchmark harness.

All five honor `~/.claude/settings.json` and the standard ecosystem hooks /
MCP / skills conventions, all five persist runs under
`~/.chimera/eventlog/<agent>-<utc>-<uuid>/`, and all five accept any
provider Chimera supports (Anthropic, OpenAI, Google, Ollama, Modal,
OpenRouter, llama.cpp, any OpenAI-compatible endpoint). What differs is the
posture, the defaults, and the I/O envelope.

> **Trademark hygiene.** Each CLI is *modelled on* a real-world upstream
> coding agent. We never name those upstream brands in live source/docs;
> filesystem-fact path mentions (`~/.claude/settings.json`,
> `~/.codex/config.toml`, `~/.little-coder/`, etc.) are kept because they
> describe an existing on-disk layout we ingest, not a brand claim. Each
> agent ships a `security-and-trademarks.md` note alongside its quickstart.

## Use-case comparison

The fastest way to pick: read the row that matches your workflow, then jump
to that agent's quickstart.

| If your use case is… | Reach for | Why |
|---|---|---|
| TUI-style agent, slash commands, keyboard-driven, daily-driver feel | **mink** | Mirrors the most common terminal-coding-agent UX; runs/agents subcommands; rich TTY chrome with auto-disable when piping. |
| Driving the same agent from CLI + REPL + HTTP/SSE + ACP, multiple clients | **otter** | One ReAct loop, four transports; server token auth; the SSE event stream is exactly the same shape as the REPL's. |
| Sandbox-first; safe-by-default; single-flag approval presets; IDE plugin via ACP; optional remote-driven sessions | **ferret** | Sandbox modes block at the OS level, approval presets block at the policy level — two flags compose. `serve` is ACP-by-default, HTTP is opt-in. |
| Minimal harness; you want to embed in your own Python app, or drive the agent from another tool over JSON-RPC, with no chrome in the way | **weasel** | Four operating modes share one loop and one session store; almost no slash commands; `from chimera.weasel.sdk import Agent` for embedding. |
| You want to run a coding agent against a small *local* model (Qwen3.5-9B, Qwen3.6-35B-A3B MoE, etc.) on llama.cpp / Ollama and benchmark it | **shrew** | Defaults pinned to `qwen3.6-35b-a3b` on llama.cpp; restricted `Read,Write,Edit,Bash` toolkit; MoE-aware context sizing; built-in Aider Polyglot / GAIA / terminal-bench harness. |
| Reproducing benchmark numbers (HumanEval, SWE-bench, AIMO) | **otter** for cloud frontier models; **shrew bench** for small local models | Otter has the cleanest one-shot mode for HumanEval-style runs; shrew's bench harness is tuned for small-model latency / loop-detection profiles. |
| Building your *own* coding agent on top of Chimera primitives | (any of them — start with the closest posture, then peel back) | Every agent is a thin CLI over `chimera.assembly.coding_agent.CodingAgent`; the quickstarts are the best on-ramp into the library. |

## Surface comparison at a glance

What each CLI ships, side by side. Read top-to-bottom for one agent or
left-to-right for one feature.

| Surface | mink | otter | ferret | weasel | shrew |
|---|---|---|---|---|---|
| One-shot (`-p`) | yes | yes | yes | yes | yes |
| Interactive REPL | rich TUI | streaming + slash palette | streaming + sandbox/approval slash commands | streaming, sparse slash commands | streaming, weasel parity |
| HTTP server | no | `serve --port` (HTTP+SSE) | `serve --http` (opt-in) | no (use RPC mode) | no |
| ACP / JSON-RPC | no | `serve --acp` (stdio) | `serve` is ACP-default (stdio) | `--mode rpc` (stdio JSON-RPC) | inherited from weasel |
| Embeddable SDK | via `chimera.assembly` | via `chimera.assembly` | via `chimera.assembly` | `from chimera.weasel.sdk import Agent` | inherits weasel SDK |
| Slash-command palette | 19 | 26 | weasel + `/sandbox`, `/approval`, `/bridge` | 7 (sparse on purpose) | weasel parity |
| Permissions model | `~/.claude/settings.json` rules | `~/.claude/settings.json` rules | `--sandbox` × `--approval` (compose) | host-defined (no built-in presets) | weasel + restricted tool default |
| Default tool set | full agent group | full agent group | full agent group | full agent group | `Read,Write,Edit,Bash` |
| Default `--max-steps` | 50 | 50 | 50 | 50 | 30 |
| Sub-agents / plan mode | yes | yes | yes | no (by design) | inherits from weasel |
| Auto-discovered extensions dir | no | no | no | `.weasel/extensions/` | `chimera/shrew/skills/` + `~/.shrew/skills/` |
| Cloud bridge | no | no | `chimera ferret bridge` | no | no |
| Built-in benchmark harness | no | (use top-level eval CLI) | no | no | `chimera shrew bench …` |
| Session prefix on disk | `mink-<utc>-<uuid>` | `otter-<utc>-<uuid>` | `ferret-<utc>-<uuid>` | `weasel-<utc>-<uuid>` | `shrew-<utc>-<uuid>` |

## Provider chain comparison

Each CLI ships its own provider resolution order — the order an agent walks
when neither `--model` nor an agent-specific env var is set. The pattern is
the same in all five (CLI flag → agent env var → provider env var → friendly
error), but the *order* of the provider env vars differs based on the
posture each agent is mirroring.

| Step | mink | otter | ferret | weasel | shrew |
|---|---|---|---|---|---|
| 1 | `--model` | `--model` | `--model` | `--model` | `--model` |
| 2 | `$MINK_MODEL` | `$OTTER_MODEL` | `$FERRET_MODEL` | `$WEASEL_MODEL` | `$SHREW_MODEL` |
| 3 | Ollama-on-`:11434` → `kimi-k2.6:cloud` | Anthropic / OpenAI / OpenRouter / Ollama (in that order) | `$OPENAI_API_KEY` → `gpt-5` (fallback `gpt-4o`) | `$ANTHROPIC_API_KEY` → `claude-sonnet-4-6` | llama.cpp on `127.0.0.1:8888` → `qwen3.6-35b-a3b` |
| 4 | `$ANTHROPIC_API_KEY` → `claude-sonnet-4-6` | (continues through the rest of the chain) | `$ANTHROPIC_API_KEY` → `claude-sonnet-4-6` | `$OPENAI_API_KEY` → `gpt-4o` | Ollama on `:11434` → first installed tag |
| 5 | `$OPENAI_API_KEY` → `gpt-4o` | — | `$OPENROUTER_API_KEY` → `openai/gpt-5` | `$OPENROUTER_API_KEY` → `anthropic/claude-sonnet-4` | cloud fallback via `--model vendor/name` (`anthropic/claude-haiku-4-5`, `openai/gpt-4o-mini`, etc.) |
| 6 | friendly error | friendly error | friendly error | Ollama on `:11434` → first installed tag, then friendly error | friendly error |

The takeaway: **mink** prefers Ollama-local first (matches its TUI-daily-driver
posture), **otter** is provider-agnostic with no strong opinion, **ferret**
prefers OpenAI-flagship models (matches its IDE-flagship posture), **weasel**
prefers Anthropic but happily falls through to anything else, and **shrew**
prefers a llama.cpp server on the loopback (matches its small-local-model
posture). All five accept `--model vendor/name` to override.

For the full provider matrix per agent — including auth env vars, base-URL
overrides, and Ollama/llama.cpp specifics — see each quickstart's
`providers.md` sibling:

- [`docs/mink/providers.md`](mink/providers.md)
- [`docs/otter/providers.md`](otter/providers.md)
- [`docs/ferret/providers.md`](ferret/providers.md)
- [`docs/weasel/providers.md`](weasel/providers.md)
- (Shrew's provider matrix is documented inline in `docs/shrew/quickstart.md`
  alongside the small-model-setup walkthrough.)

## When to pick which

A short decision tree, in plain prose:

**Pick mink** when you want a polished TUI that *feels like* the
terminal-coding-agent you already use day-to-day, and when most of your
time is spent inside one terminal session. Mink's slash-command palette,
streaming text, and runs/agents subcommands are tuned for that workflow.
It's also the right pick if you want to run against `kimi-k2.6:cloud` or a
local Ollama tag with zero ceremony.

**Pick otter** when you need to drive *the same* agent loop from more than
one client — for example, a one-shot CLI in a CI job *plus* a long-running
HTTP+SSE server fronting a web UI *plus* an IDE plugin speaking ACP. Otter's
contract is that the loop, the tool registry, and the session store are
identical across all four transports; only the I/O envelope changes. Pick
otter when "consistent semantics across clients" matters more than rich
TUI chrome.

**Pick ferret** when sandbox + approval is non-negotiable and you want
single-flag presets rather than fine-grained tool allowlists. Ferret's
`--sandbox` flag blocks at the OS level (process / network / filesystem
namespacing) and `--approval` blocks at the policy level (read-only / auto /
full); a tool call has to pass both. Pick ferret when you're driving an
agent against unfamiliar code, when the agent runs unattended, when an
IDE plugin is the primary interface (ferret's `serve` is ACP-by-default),
or when a remote UI needs to drive a local agent (`chimera ferret bridge`).

**Pick weasel** when you want to *embed* the agent rather than run it as a
CLI — `from chimera.weasel.sdk import Agent` is sync-or-async, returns
text + cost + structured events, and lives inside your own process. Also
pick weasel when you're driving the agent from another *tool* (CI runner,
custom dashboard, build script) over stdio JSON-RPC, when the chrome of
mink/otter/ferret would be in your way, or when you want to ship your own
extensions / hooks / slash commands without working around an opinionated
default set. Weasel's posture: powerful defaults, no chrome, four
interchangeable I/O envelopes, you-do-you on everything else.

**Pick shrew** when the model lives on your laptop. Shrew's whole thesis is
"scaffold-model-fit": small models (Qwen3.5-9B, Qwen3.6-35B-A3B MoE) need
fewer tools, fewer steps, and a tighter system prompt — give them too many
tools or too long a horizon and they regress. Shrew layers three
small-model adjustments onto weasel: MoE-aware context sizing, scaffold-fit
prompt wrapping, and tool-list trimming. Pick shrew when you want to run an
agent on llama.cpp, when you're benchmarking small models on Aider Polyglot
/ GAIA / terminal-bench, or when latency / cost / privacy keep you off the
cloud frontier. Cloud fallbacks (`anthropic/claude-haiku-4-5`,
`openai/gpt-4o-mini`) work via `--model vendor/name` so you can A/B against
a known frontier model without leaving shrew.

## How they relate underneath

All five CLIs are thin wrappers over the same library:

```
chimera/<agent>/cli.py        ← argparse + small per-agent defaults
                ↓
chimera/assembly/coding_agent.CodingAgent
                ↓
chimera/core/loop.AgentLoop  +  chimera/core/tool_group.create_default_tools(...)
                ↓
chimera/providers/factory.create_provider(model=..., ...)
                ↓
chimera/sessions/eventlog (file-locking, gap-detection, crash-recovery)
                ↓
~/.chimera/eventlog/<agent>-<utc>-<uuid>/
```

Anything you change in the library — a new tool, a new compaction strategy,
a new provider adapter, a new permission backend — flows through to all
five CLIs at once. That's why we ship five thin agents instead of one
opinionated mega-agent: pick the posture, inherit the substrate.

If you want to build your own coding agent in this style — same substrate,
your own posture — see the [Build Your Own Agent](playbooks/08-building-agents.md)
playbook (988 lines, full library guide).

## Cross-links

Per-agent quickstarts (each has its own `providers.md`,
`security-and-trademarks.md`, and posture-specific docs):

- [Mink quickstart](mink/quickstart.md) — TUI-first
- [Otter quickstart](otter/quickstart.md) — server-first / multi-client
- [Ferret quickstart](ferret/quickstart.md) — IDE-flagship sandbox-first
- [Weasel quickstart](weasel/quickstart.md) — minimal harness, four modes
- [Shrew quickstart](shrew/quickstart.md) — small-local-model

Adjacent reading:

- [Build Your Own Agent](playbooks/08-building-agents.md) — full developer guide
- [Architecture overview](https://0bserver07.github.io/chimera/architecture/) — the 8-layer stack
- [All Playbooks](playbooks/) — 13 end-to-end recipes
- [Benchmarks](benchmarks/README.md) — transparency framework, raw data
- [Mink benchmark adapters](mink/benchmarks.md) — every adapter under `chimera/eval/benchmarks/`
