# `chimera mink` Quickstart

## What this is

`chimera mink` is a Claude-Code-equivalent REPL built on Chimera's existing `AgentLoop`, `LoopConfig`, tool registry, permissions, and session primitives. Its default backend is **Kimi K2.6** served by Ollama Cloud (`kimi-k2.6:cloud`). This page covers the M0 walking skeleton: a single-process script that drives a real ReAct loop end-to-end against Kimi via Ollama, with streamed text and tool calls. M1+ adds the `chimera mink` subcommand, the slash-command surface, `.claude/settings.json` parity, and the markdown TUI.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com/download) 0.7 or newer (streaming + tool calls landed in PR #10415, May 2025)
- An Ollama account for cloud tags

```bash
uv --version                          # >= 0.4
ollama --version                      # >= 0.7
uv sync --extra dev                   # core deps
```

The Ollama provider lives in `chimera/providers/ollama.py` and is part of the core install — no extras required.

## Provision the model

```bash
ollama signin                         # required for any :cloud tag
ollama run kimi-k2.6:cloud            # warm the cloud endpoint; Ctrl-D to exit
ollama pull qwen3:32b                 # local fallback (parallel tools, 128k ctx)
```

Notes:

- `ollama pull kimi-k2.6` does **not** work. K2.6 is 1T params (~600 GB at Q4); Ollama exposes it only as a cloud tag. See [report 21 — Kimi K2.6](../../research/mink/21-kimi-k2.6.md) §8.
- The first `ollama run kimi-k2.6:cloud` after sign-in does an auth handshake; subsequent calls are warm.
- `qwen3:32b` is the recommended local fallback: native tool calls, 131072 context, runs on a 24 GB GPU at usable speed.

If your Ollama daemon is on another host, point the provider at it:

```bash
export OLLAMA_HOST=http://gpu-box.lan:11434
```

## Run the walking skeleton

```bash
uv run python examples/mink_walking_skeleton.py "list files then read README.md"
```

Expected output shape:

```text
[warn] kimi-k2.6:cloud unavailable; falling back to qwen3:32b      # only if fallback engaged (stderr)
I'll list the repo first, then read the README.

▶ Bash$ ls
CHANGELOG.md  CLAUDE.md  README.md  chimera/  docs/  examples/  tests/

▶ Read(README.md)
# Chimera
A composable coding agent framework
...

The repo root has a README pitching Chimera as a composable coding agent framework.

--- DONE --- steps=3 ok=True
```

Streaming text appears as it arrives. Tool calls render as `▶ <Tool>(<args>)` lines followed by the tool result. The trailing `--- DONE ---` line reports loop steps and success. Ctrl-C cancels the in-flight stream within ~1 second and exits 130.

## Env vars

| Variable | Default | Meaning |
|---|---|---|
| `CHIMERA_MINK_MODEL` | `kimi-k2.6:cloud` | Primary model tag passed to Ollama. Any tool-capable Ollama tag works. |
| `CHIMERA_MINK_FALLBACK` | `qwen3:32b` | Used if the primary model errors at provider construction (auth, missing tag, network). |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama daemon URL. Set when the daemon runs on another host. |

## What works in M0

- Streaming assistant text via `/api/chat?stream=true` (mid-stream NDJSON chunks)
- Native tool calls accumulated from `done:false` chunks: `Bash`, `Read`, `Write`, `Edit`, `Grep` (search), `Glob` (list_files), `TodoWrite`
- Ctrl-C cancellation through `CancellationToken` — the walking skeleton
  creates a fresh token locally and relies on the natural `KeyboardInterrupt`
  bubble-up at exit. The "10 s thread join" graceful path documented in
  `CLAUDE.md` is wired only in the interactive REPL (`chimera mink` without
  `-p`), not in `examples/mink_walking_skeleton.py`.
- Automatic fallback from `CHIMERA_MINK_MODEL` to `CHIMERA_MINK_FALLBACK` on provider construction failure
- `num_ctx` per-request (`262144` for Kimi, `131072` for Qwen3) and `keep_alive: "60m"` so the cloud endpoint stays warm across ReAct steps
- `think: true` for Kimi, with `reasoning_content` preserved across tool turns

## What does NOT work yet (M1+)

- The `chimera mink` subcommand — M0 ships only the example script
- Rich TUI (markdown rendering, spinner, collapsed thinking blocks, tool-block expand/collapse) — M1
- Slash commands beyond `Ctrl-D` / process exit — M1 adds `/status`, `/doctor`, `/permissions`, `/hooks`, `/mcp`, `/resume`, `/cost`, `/compact`, `/sandbox`, `/subagent`, `/plugin`, `/review`, `/config`
- Drop-in `.claude/settings.json` loader and `permissions.allow/ask/deny` rule grammar — M2
- PreToolUse hook `updatedInput` mutation — M2
- MCP servers and `mcp__server__tool` namespacing — M3
- Subagents via `Task` tool and `.claude/agents/*.md` — M3
- `/resume <session_id>` and `/compact` as in-CLI commands — M4

## Known limits of Kimi K2.6 `:cloud`

Cited from [report 21 — Kimi K2.6](../../research/mink/21-kimi-k2.6.md):

- **Weights stay on Moonshot/Ollama infrastructure.** Cloud-only tag. Prompts and tool inputs are visible to the cloud operator. If that is unacceptable, self-host K2.6 with vLLM/SGLang/KTransformers from the HF safetensors (~600 GB disk, workstation hardware) and point `OLLAMA_HOST` at a compatible bridge — or pick a local model.
- **`format` (JSON-schema grammar) is not honored on `:cloud`.** Cloud-served Kimi ignores the `format` field. Use prompt-level JSON instructions plus `temperature: 0` for structured outputs. Local models (`qwen3:32b`) honor `format` normally.
- **Vision is weak.** BabyVision 39.8% — the lowest relative score on Moonshot's own card. Strong text/coding model, but do not route image-heavy tasks to it.
- **License is modified MIT.** Standard MIT below thresholds. If your product crosses **>100M MAU or >$20M/month revenue**, you must display "Kimi K2" attribution in your UI. No royalties.
- **Reasoning persistence is mandatory.** In multi-turn tool loops the server errors if `reasoning_content` is dropped from history. The provider preserves it; do not strip thinking traces in custom compaction.
- **`tool_choice: "required"` is forbidden when `think: true`.** Use `auto` or `none`.

## Persistence and opt-out (audit M-11)

Every `chimera mink -p` invocation persists the user prompt, agent
result, and a `summary.json` to `~/.chimera/eventlog/mink-<id>/` by
default. This is local-only — Chimera never phones home — but it is
on disk in plaintext and can include sensitive prompts and tool args.

To disable persistence for a single run, pass `--no-save`:

```bash
chimera mink -p "explain this repo" --no-save
```

To inspect what was saved, list the eventlog directory or use the
`runs` subcommand companion (when available):

```bash
ls ~/.chimera/eventlog/
cat ~/.chimera/eventlog/mink-<id>/summary.json
```

To purge old runs, simply delete the directory:

```bash
rm -rf ~/.chimera/eventlog/mink-*
```

There is no remote telemetry, error reporting, or analytics in the
mink CLI; the only network egress is the LLM provider call you
explicitly configured (Ollama, Anthropic, OpenAI, …).

## Troubleshooting

**`ollama signin` fails or hangs.** Check that `ollama --version` is 0.7+. Older builds shipped before cloud auth. Re-run after upgrade. If the browser flow doesn't open, run `ollama signin --help` for the device-code flow.

**`model 'kimi-k2.6:cloud' not found`.** You did not sign in, or you typed `kimi-k2.6` without the `:cloud` suffix. Re-run `ollama signin`, then `ollama run kimi-k2.6:cloud` once to confirm. The local pull (`ollama pull kimi-k2.6`) is not supported.

**First call takes 10–30 seconds.** Cold start on the cloud endpoint. Subsequent calls within 60 minutes reuse the warm instance because the provider sets `keep_alive: "60m"`.

**`tool_calls` is always empty / model never invokes a tool.** Make sure the provider is hitting `/api/chat`, not `/v1/chat/completions`. The OpenAI-compat layer silently drops `tool_calls` when streaming (Ollama issues #9632, #12557). The Chimera provider uses the native endpoint by default; if you set a custom `base_url`, it must end in the host root, not `/v1`.

**Streaming text appears but tool calls never fire on a local model.** Confirm the model has the `tools` capability. Check `ollama show <model>` — `Capabilities: tools` must be listed. `qwen3:32b`, `llama3.1:70b-instruct`, and `kimi-k2.6:cloud` all have it; many community quants do not.

**`num_ctx` defaults to 4096 and the agent forgets the system prompt.** The provider should be passing `num_ctx` per request. If you see `prompt_eval_count` capped near 4096, the request is missing `options.num_ctx`. Verify with `OLLAMA_DEBUG=1 ollama serve` and inspect the incoming JSON.

**`Tool result message rejected`.** Tool result messages must use `{"role": "tool", "tool_name": "<name>", "content": "<string>"}`. The provider builds this; if you patched `_convert_messages()`, ensure `tool_name` is present and `content` is a string (stringify JSON results).
