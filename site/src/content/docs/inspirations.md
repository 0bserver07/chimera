---
title: Inspirations
description: Public map of which upstream coding agent inspired each Chimera CLI codename.
---

# Inspirations

Chimera ships **seven coding-agent CLIs**, each modelled on a real-world tool. The "inspired by" column below names the upstream that motivated the design. Chimera reimplements the *posture* — sandbox-first, server-first, etc. — using its own primitives (the 8-phase architecture documented in [`docs/architecture.md`](./architecture.md)).

| Codename | Alias | Inspired by | One-liner |
|---|---|---|---|
| **mink** | `tui` | Claude Code (Anthropic) | TUI-first interactive coding agent; ingests `~/.claude/settings.json` |
| **otter** | `multi` | opencode | Server-first, multi-client (HTTP + SSE + ACP); plugin-driven |
| **ferret** | `sandbox` | codex (OpenAI) | Sandbox-first execution + IDE-flagship + OpenAI-default provider chain |
| **weasel** | `mini` | pi (pi-mono) | Minimal harness, four operating modes (interactive / print / RPC / SDK) |
| **shrew** | `tiny` | little-coder | Tuned for small local models (llama.cpp, Qwen MoE, scaffold-fit) |
| **stoat** | `shell` | kimi-cli (Moonshot) | Shell-mode toggle (Ctrl-X); Kimi-tuned defaults |
| **badger** | `strict` | claw-code | Harness-rewrite posture: tighter defaults, parity tracking, `--rerun-on-failure` |

Codenames are **stable shipping names** (used in commits, release notes, parity matrices). Aliases are **for discoverability**: `chimera mink` and `chimera tui` are exactly equivalent.

## Per-codename detail

### mink ↔ tui (Claude Code)

The TUI-first ergonomic. Lives at `chimera/mink/` with a slash-command palette mirroring the upstream's `/help`, `/clear`, `/cost`, `/context`, etc. Reads `~/.claude/settings.json` for hooks + permissions + MCP servers. Ships per-CLI presets, sessions list/show/cost/share, run persistence, and a stream-json output mode.

Chimera's mink adopted the TUI ergonomics + settings ingest. It diverged on persistence (event-sourced JSONL + SQLite snapshots) and provider routing (multi-provider chain, not Anthropic-only).

See `docs/mink/parity-matrix.md`.

### otter ↔ multi (opencode)

The server-first, multi-client posture. Lives at `chimera/otter/`. Default `serve` transport is HTTP + SSE on port 5173; alternative is ACP (`serve --acp`) for IDE-style JSON-RPC over stdio. Plugin-driven extension model with `~/.opencode/plugin/` discovery, MCP server config ingest, custom `.opencode/command/*.md` slash commands.

Chimera's otter adopted the server + multi-transport architecture. It diverged on file layout (eventlog under `~/.chimera/eventlog/otter-*` rather than upstream's session DB) and on the default cost-tracking surface.

See `docs/otter/parity-matrix.md`.

### ferret ↔ sandbox (codex)

The sandbox-first, IDE-flagship posture. Lives at `chimera/ferret/`. Every shell command runs through a `SandboxedEnvironment` by default (read-only / workspace-write / workspace-write-network); approval presets bundle the entire permission stance into one flag. ACP is the default `serve` transport (IDE-shaped notifications: `code/diff`, `editor/open_file`, `terminal/output`); HTTP is opt-in via `--http`. macOS adds OS-level `sandbox-exec` wrapping.

Chimera's ferret adopted the sandbox + IDE ergonomics + OpenAI-default provider chain. It diverged on the language (Python on Chimera primitives rather than the upstream's Rust core).

See `docs/ferret/parity-matrix.md`.

### weasel ↔ mini (pi-mono)

The minimal-harness posture. Lives at `chimera/weasel/`. Four operating modes (interactive REPL / one-shot print / RPC stdio / embeddable SDK). Skips features like sub-agents and plan mode by default; ships powerful defaults that can be extended via `.weasel/extensions/*.{js,ts,py}` (Python today, Node subprocess for JS/TS). Theme + prompt-template registries.

Chimera's weasel adopted the four-mode architecture + npm-extensible philosophy. It diverged by translating the TS/JS extension model to a Python-first one with a Node bridge for shipped TS extensions.

See `docs/weasel/parity-matrix.md`.

### shrew ↔ tiny (little-coder)

The small-model-tuned posture. Lives at `chimera/shrew/`. Built on top of weasel; pins small-model defaults (`qwen3.6-35b-a3b` via llama.cpp, `--max-steps 30`, restricted tool subset). Curated skill markdowns under `chimera/shrew/skills/{knowledge,protocols,tools}/`. Small-model-fit extensions (MoE-aware context sizing, scaffold-fit prompt wrapping, tool filtering for sub-9B models).

Chimera's shrew adopted the scaffold-model-fit thesis (skills + extensions + tighter budgets). It diverged on packaging (Python module vs the upstream's npm package).

See `docs/shrew/parity-matrix.md`.

### stoat ↔ shell (kimi-cli)

The shell-mode-toggle posture. Lives at `chimera/stoat/`. The signature feature is `/shell` (or Ctrl-X) — flips the REPL between agent mode (`stoat>` prompt, prompts feed the LLM) and shell mode (`stoat$` prompt, inputs run as `bash -c`). Kimi-first provider chain (`$MOONSHOT_API_KEY` → `kimi-k2.6` → fallbacks).

Chimera's stoat adopted the shell-mode toggle. It diverged on language (Python harness on Chimera primitives rather than the upstream's Python harness on different primitives).

See `docs/stoat/parity-matrix.md`.

### badger ↔ strict (claw-code)

The harness-rewrite posture. Lives at `chimera/badger/`. Tighter defaults (`--max-steps 25` vs 50 elsewhere), opt-in `--rerun-on-failure` with bounded retries, and a distinctive `chimera badger parity --against PARITY.md` subcommand that diffs live agent state against a declared schema (rc=0 match / 1 drift / 2 usage error).

Chimera's badger adopted the "better harness tools" thesis + parity-tracker UX. It diverged on language (Python vs the upstream's Rust port).

See `docs/badger/parity-matrix.md`.

## Trademark policy

Live source / docs / help text in Chimera **never embeds upstream brand strings**. This document and `chimera agents` discovery output are explicit factual references — they exist so users and contributors can know what they're getting — and are excluded from each CLI's trademark scrub via a `SKIP_FILES` regex.

See `docs/<cli>/security-and-trademarks.md` for the per-CLI policy and `scripts/<cli>_trademark_scrub.sh` for the enforcement script.

## See also

- [Architecture (8 phases)](./architecture.md) — how all 7 CLIs compose from the same Chimera primitives.
- [Coding agents overview](./coding-agents.md) — comparison table + use-case decision guide.
- Per-CLI quickstarts under `docs/<cli>/quickstart.md`.
