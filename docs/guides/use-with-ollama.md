---
title: "Use with Ollama"
description: "Two paths: Ollama Cloud direct API (no localhost) or the local daemon. Both work with all 7 Chimera CLIs."
---

There are **two ways** to drive Chimera with Ollama, both supported as of v0.7.1:

| Path | Endpoint | Auth | What runs the model |
|---|---|---|---|
| **A. Ollama Cloud direct** | `https://ollama.com` | `Authorization: Bearer $OLLAMA_API_KEY` | Ollama's GPUs (their datacenter) |
| **B. Local daemon (passthrough or local)** | `http://localhost:11434` | none (or `ollama signin` for cloud passthrough) | Local daemon → forwards `:cloud` ids to Ollama / keeps non-cloud ids local |

Path A is simpler — no daemon, no `ollama serve`, just two env vars and any of the 7 CLIs. Path B is what you use if you also want **truly local** models on your own GPU.

---

## Path A — Ollama Cloud direct (recommended for cloud models)

### Setup

1. Sign up at [ollama.com](https://ollama.com)
2. Generate an API key at [ollama.com/settings/keys](https://ollama.com/settings/keys)
3. Set two env vars and you're done:

```bash
export OLLAMA_API_KEY=your_key_here
export OLLAMA_HOST=https://ollama.com
```

That's it. No daemon, no `ollama serve`, no `ollama pull`. Requests go straight to Ollama's API with bearer-token auth.

### Verify

List the models your key has access to:

```bash
curl -s -H "Authorization: Bearer $OLLAMA_API_KEY" https://ollama.com/api/tags | jq '.models[].name'
```

You should see ~30-40 cloud models including `gpt-oss:120b`, `glm-5.1`, `qwen3-coder:480b`, `kimi-k2.6`, `deepseek-v4-pro`.

### Run any of the 7 CLIs

```bash
# Pick the codename for your task — chimera which --task "..." helps
chimera mink   --model gpt-oss:120b-cloud  -p "explain this repo"
chimera otter  --model glm-5.1:cloud       -p "summarize TODO comments"
chimera ferret --model deepseek-v4-pro     --sandbox workspace-write -p "fix tests"
chimera weasel --model gpt-oss:120b-cloud  -p "list py files in chimera/" --json
chimera shrew  --model qwen3-coder:480b-cloud -p "explain this directory"
chimera stoat  --model kimi-k2.6-cloud     -p "what is 8 - 2?"
chimera badger --model glm-5.1:cloud       -p "verify the test suite passes"
```

The `-cloud` and `:cloud` suffixes work interchangeably. When `$OLLAMA_HOST` points at `ollama.com`, Chimera strips the suffix automatically for the direct API. When pointed at a local daemon, it keeps the suffix so the daemon knows to forward.

### Sessions to disk

Every run is event-sourced. Inspect and resume:

```bash
chimera mink runs list
chimera mink runs show mink-20260514T034436-46db8b28
chimera resume <run-id>          # auto-detects which CLI saved it
```

---

## Path B — Local daemon (for truly local models + cloud passthrough)

### Setup

1. [Download Ollama](https://ollama.com/download) and run `ollama serve` (or just let the app boot).
2. Optional: pull local models. `ollama pull qwen3-coder-30b`, `ollama pull glm-4.7-flash`, etc.
3. Optional: `ollama signin` to enable `:cloud`-suffixed passthrough.
4. Point Chimera at the daemon. Either drop in via the Anthropic-compatible adapter:

```bash
export ANTHROPIC_BASE_URL=http://localhost:11434
export ANTHROPIC_AUTH_TOKEN=ollama
chimera mink --model glm-5.1:cloud -p "..."
```

…or use the native Ollama provider (and don't touch `ANTHROPIC_*`):

```bash
unset OLLAMA_HOST OLLAMA_API_KEY ANTHROPIC_BASE_URL
chimera shrew --model qwen3-coder:30b -p "..."   # local, free, your hardware
chimera shrew --model glm-5.1:cloud -p "..."     # daemon forwards to Ollama Cloud
```

Use this path when you want any of:

- A local model running on your own GPU / Apple Silicon
- The daemon's `keep_alive` model caching for many small turns
- SSH-key based cloud auth (handled by `ollama signin`)

---

## Recommended models (as of 2026-05-13, on ollama.com)

| Model id | Hosting | Approx context | Best for |
|---|---|---|---|
| `gpt-oss:120b-cloud` | Cloud | 128k | General-purpose, fast, free at time of writing |
| `glm-5.1:cloud` | Cloud | 128k+ | Coding / refactoring; Chimera benchmarks against it |
| `kimi-k2.6:cloud` | Cloud | 200k+ | Long sessions, tool-use heavy |
| `kimi-k2-thinking` | Cloud | 1M+ | Extended thinking, reasoning-heavy |
| `qwen3-coder:480b-cloud` | Cloud | 131k | Pure coding tasks |
| `deepseek-v4-pro` | Cloud | 262k | Latest DeepSeek; reasoning + code |
| `minimax-m2.7:cloud` | Cloud | 1M+ | Very long contexts |
| `qwen3-coder` | Local (~60GB) | 131k | Local coding on Apple Silicon / GPU |
| `glm-4.7-flash` | Local | 128k | Local coding, smaller footprint |

Browse the full live catalog:

```bash
curl -s -H "Authorization: Bearer $OLLAMA_API_KEY" https://ollama.com/api/tags | jq '.models[] | {name, size_gb: (.size / 1e9 | round)}'
```

---

## Per-CLI tips

- **`chimera mink`** — TUI-first, slash-command palette. Reads `~/.claude/settings.json` for hooks / permissions / MCP. Best for interactive sessions where you want a REPL with rich rendering.
- **`chimera otter`** — Multi-client server. Three transports (one-shot, HTTP+SSE, ACP). Use when you want a long-running agent server other tools connect to.
- **`chimera ferret`** — Sandbox-first. `--sandbox read-only|workspace-write|danger-full-access` + `--approval suggest|auto|yolo`. Best for *destructive* tasks where you want a safety net.
- **`chimera weasel`** — Minimal harness. Four modes: interactive REPL, `-p` print, JSON-RPC over stdio, embedded SDK. Best for scripting / pipelines / programmatic use.
- **`chimera shrew`** — Tuned for small local models. Smaller default `--max-steps`, restricted default tools, output-parser repair for text-mode tool calls, quality monitor for empty/hallucinated responses.
- **`chimera stoat`** — Shell-mode toggle (Ctrl-X). Each REPL line either feeds the LLM or runs as a bash command. Mode-tagged history.
- **`chimera badger`** — Strict harness posture. Tighter `--max-steps`, `--rerun-on-failure`, parity tracking. Best for repeatable / regression-style work.

Run `chimera agents` to see this same matrix any time. Or `chimera which --task "describe what you want"` to get a recommendation.

---

## Troubleshooting

**`HTTP 401 — Auth rejected by upstream`**

Either `$OLLAMA_API_KEY` is wrong, expired, or unset. Verify with the curl above.

**`HTTP 404 ... /v1/chat/completions`**

Your shell still has a stale `$ANTHROPIC_BASE_URL` pointing at `localhost:11434` while no daemon is running. `unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN`.

**`HTTP 503` — Upstream issue**

Ollama's upstream is having trouble. The provider returns a friendly error and a hint. Retry, or switch model: `--model glm-5.1:cloud` is usually up when others are down.

**`stoat: $MOONSHOT_API_KEY is required for kimi-* models`** (fixed in v0.7.1+)

Use the `-cloud` or `:cloud` suffix: `chimera stoat --model kimi-k2.6-cloud`. Bare kimi ids still need a Moonshot key.

**Models are slow on first call**

The daemon (path B) does cold-loads. Subsequent calls are fast due to `keep_alive`. Path A has no cold-load.

---

## Cost & privacy

| Path | Cost | Privacy |
|---|---|---|
| A: Cloud direct | Free at time of writing (Ollama's loss-leader pricing). May change. | Calls + payloads go to Ollama's servers. |
| B: Local daemon, non-cloud model | $0 | Stays on your machine. |
| B: Local daemon, `:cloud` model | Same as A — the daemon just proxies. | Same as A. |

For sensitive code, prefer **Path B with a non-cloud model**.

---

## Next

- [Architecture](/chimera/architecture/) — how the 8 phases compose
- [Per-CLI quickstarts](/chimera/mink/quickstart/) — `chimera mink` deep-dive (and otter / ferret / weasel / shrew / stoat / badger)
- [Inspirations](/chimera/inspirations/) — which upstream tool inspired each codename
