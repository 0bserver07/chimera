# `chimera mink` Quickstart

## What this is

`chimera mink` is a coding-agent REPL built on Chimera's existing `AgentLoop`, `LoopConfig`, tool registry, permissions, and session primitives. Its default backend is Ollama with `glm-5.1:cloud` (or `kimi-k2.6:cloud` for the walking-skeleton example). The runtime is a single-process script that drives a real ReAct loop end-to-end with streamed text + tool calls, the `chimera mink` subcommand and slash-command surface, drop-in `settings.json` ingest (permissions + hooks), and a rich markdown TUI. Run `chimera mink --help-long` to see verbose per-flag descriptions; `--help` itself stays under 50 lines for scannability.

## Provider choice

Mink talks to LLMs through Chimera's standard provider stack: Ollama (local + cloud tags), Anthropic, OpenAI, Google, and any OpenAI- or Anthropic-compatible endpoint. The full matrix — auth env vars, latency notes, tool-call quirks, and known limits per backend — lives in [`providers.md`](providers.md).

For evaluation, see [`benchmarks.md`](benchmarks.md): every adapter under `chimera/eval/benchmarks/` (SWE-bench, HumanEval, SWT-Bench, SWE-PolyBench, FeatureBench, Cline Bench, DPAI Arena, tau-bench, Context-Bench, HumanEval+, MBPP, LiveCodeBench, MATH-500/AIMO, Custom), its status, and how to drive it through the harness.

Quick recommendation: Ollama with `glm-5.1:cloud` is the friendliest path (cheap, fast, good tool calling). The built-in mink default is `kimi-k2.6:cloud` for parity with the original walking skeleton; pass `--model` or set `CHIMERA_MINK_MODEL` to switch. Anthropic API works without extra setup too: `chimera mink --model claude-sonnet-4-6` after `export ANTHROPIC_API_KEY=...`.

## Model selection

A short reference of working tags per [`models.md`](models.md):

| Backend | Tag | Notes |
|---|---|---|
| Ollama Cloud | `glm-5.1:cloud` | Recommended default; fast, cheap, native tool calls |
| Ollama Cloud | `kimi-k2.6:cloud` | Built-in mink default; long context |
| Ollama local | `qwen3:32b` | Local fallback; 131k context, runs on a 24 GB GPU |
| Anthropic | `claude-sonnet-4-6` | Strongest tool calling; needs `ANTHROPIC_API_KEY` |
| OpenAI | `gpt-4o` | Needs `OPENAI_API_KEY`; route via OpenAI-compat |

`--model` always wins over `CHIMERA_MINK_MODEL`; `CHIMERA_MINK_MODEL` always wins over the built-in default. So a CI env can pin the tag once and ad-hoc invocations can still override:

```bash
export CHIMERA_MINK_MODEL=glm-5.1:cloud
chimera mink -p "summarize this repo"                       # uses glm-5.1:cloud
chimera mink --model claude-sonnet-4-6 -p "review diff"     # overrides to claude
```

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
| `CHIMERA_MINK_SETTINGS_PATH` | (unset) | Override `.claude/settings.json` discovery (see [`parity-matrix.md`](parity-matrix.md) "How to use"). |
| `CHIMERA_RICH_TUI` | (unset) | When `=1`, opt the `chimera code` REPL into the rich `MinkStreamHandler` too. |
| `NO_COLOR` | (unset) | When set to any value, force the plain handler (synonym for `--no-color`). |
| `CHIMERA_SSH_TEST_HOST` | (unset) | Live-test target for `--remote`; needed only by the SSH integration tests. |

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

> NOTE (M7, 2026-04-25): every bullet in this section shipped in v0.3.0 or
> v0.4.0. Kept as a historical milestone log; see [`parity-matrix.md`](parity-matrix.md)
> for the current shipped surface (subsystems 1–20).

- The `chimera mink` subcommand — M0 ships only the example script
- Rich TUI (markdown rendering, spinner, collapsed thinking blocks, tool-block expand/collapse) — M1
- Slash commands beyond `Ctrl-D` / process exit — M1 adds `/status`, `/doctor`, `/permissions`, `/hooks`, `/mcp`, `/resume`, `/cost`, `/compact`, `/sandbox`, `/subagent`, `/plugin`, `/review`, `/config`
- Drop-in `.claude/settings.json` loader and `permissions.allow/ask/deny` rule grammar — M2
- PreToolUse hook `updatedInput` mutation — M2
- MCP servers and `mcp__server__tool` namespacing — M3
- Subagents via `Task` tool and `.claude/agents/*.md` — M3
- `/resume <session_id>` and `/compact` as in-CLI commands — M4

## v0.4.0 surface added since the M0 milestone

The flag matrix exposed by `chimera mink --help` today (additive to the
M0/M1 set above):

| Flag / subcommand                   | Meaning                                                                         |
|-------------------------------------|---------------------------------------------------------------------------------|
| `--remote ssh://user@host[:port][/path]` | Route file/bash tools through `SSHEnvironment` (scaffold; see [`remote.md`](remote.md)). |
| `--allowed-tools Bash,Read,...`     | Comma-separated allowlist. Unknown name → exit 2 with valid set on stderr.      |
| `--tool-timeout SECONDS`            | Per-tool-call `asyncio.wait_for` ceiling.                                       |
| `--no-rich` / `--no-color`          | Force the plain handler; auto-disabled when stdout is not a TTY or `NO_COLOR` is set. |
| `--no-save`                         | Skip persistence to `~/.chimera/eventlog/mink-<id>/`.                            |
| `--run-id <id>`                     | Override the auto-generated run id (reproducible test fixtures).                 |
| `--version`                         | Print `chimera mink <version>` and exit.                                         |
| `mink runs list / show / share`     | Inspect persisted runs; `share --sink {file,gist,base64}` exports a tarball ([#129](https://github.com/0bserver07/chimera/issues/129)). |
| `mink agents list / show <name>`    | List or describe agents reachable from the project > user > built-in chain.      |

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
`runs` subcommand (see below):

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

## Inspecting your runs

`chimera mink runs list` walks `~/.chimera/eventlog/` and renders a
fixed-column table, newest first:

```text
$ chimera mink runs list
RUN_ID                                DATE              MODEL              OK   STEPS  COST   PROMPT
mink-20260424T051001-71032a5e         2026-04-24 05:10  glm-5.1:cloud      ✓    4      $0.00  list files then read README.md
mink-20260423T191222-9f0ab412         2026-04-23 19:12  kimi-k2.6:cloud    ✓    3      $0.00  explain this repo
mink-20260423T184005-2d11c7e0         2026-04-23 18:40  qwen3:32b          ✗    2      $0.00  generate tests for foo.py
```

Filter / cap the table:

```bash
chimera mink runs list --limit 5
chimera mink runs list --runs-model glm-5.1:cloud
chimera mink runs list --success-only
chimera mink runs list --failed-only
```

`chimera mink runs show <id>` prints summary metadata plus the event
transcript. Use `--no-events` for a one-pane summary, or `--full` to
force the full transcript when piping through a pager:

```bash
chimera mink runs show mink-20260424T051001-71032a5e
chimera mink runs show mink-20260424T051001-71032a5e --no-events
```

## Listing available agent presets

Mink resolves `--agent <name>` through a project > user > built-in
chain: `<cwd>/.claude/agents/*.md`, `~/.claude/agents/*.md`, and the
built-in registry (`build`, `explore`, `general`, `plan`, `review`).

```bash
$ chimera mink agents list
NAME       SOURCE   MODEL              TOOLS                              DESCRIPTION
build      builtin  -                  read,write,edit,bash,test,...      Build features end-to-end
explore    builtin  -                  read,search,list_files             Read-only exploration
general    builtin  -                  (default)                          Default coding agent
plan       builtin  -                  read,search                        Plan-only, no edits
review     builtin  -                  read,search,bash                   Code review

$ chimera mink agents show explore
```

## Pipe-friendly mode

Mink auto-detects pipes — when stdout is not a TTY (e.g. you're piping
into `tee`, `grep`, or a CI log) the rich Markdown handler turns off
and you get plain text. Force it explicitly with `--no-color` (or its
synonym `--no-rich`); `$NO_COLOR` is honored too.

```bash
chimera mink -p "summarize" --no-color | tee mink.log
chimera mink -p "ship it" --output-format json
chimera mink -p "ship it" --output-format stream-json   # one JSON line per LoopEvent
```

`--output-format json` emits a single result object on exit;
`stream-json` emits one JSON line per `LoopEvent` for downstream
pipelines.

## Where things are persisted

| Path | What |
|---|---|
| `~/.chimera/eventlog/mink-<id>/summary.json` | Per-run metadata (model, cost, steps, success, prompt) |
| `~/.chimera/eventlog/mink-<id>/event-*.json` | Full event stream for `runs show` |
| `~/.chimera/sessions/<workdir-hash>.jsonl` | Interactive REPL `SessionTree` log (used by `--resume`) |
| `~/.chimera/mcp.json`, `<cwd>/.mcp.json` | MCP server declarations loaded by `_load_mcp_tools` |
| `<cwd>/.claude/settings.json`, `~/.claude/settings.json` | Permissions + hooks (CC-format settings) |

Everything is local-only and plaintext. No remote telemetry.

## Permission mode (5-mode standard)

Mink's `--permission-mode` flag now accepts both the historical
ecosystem-parity choices **and** the cross-CLI five-mode standard
(shared with `chimera ferret` and `chimera badger`):

| Mode         | Reads | Edits | Bash / Git | Notes                                |
| ------------ | ----- | ----- | ---------- | ------------------------------------ |
| `read-only`  | allow | deny  | deny       | New name for `plan`.                 |
| `suggest`    | allow | ask   | ask        | New name for `default`.              |
| `auto`       | allow | allow | ask        | New name for `acceptEdits`.          |
| `yolo`       | allow | allow | allow      | New name for `bypassPermissions`.    |
| `strict`     | ask   | ask   | ask        | Confirm every tool call.             |

Both spelling families work — `--permission-mode plan` and
`--permission-mode read-only` produce identical policies. Use whichever
is easier to remember. The 5-mode names are recommended for new
scripts because they match the flag values you would type at
`chimera ferret` and `chimera badger`. See
[`permissions.md`](permissions.md) for the full mapping table and
[`settings.md`](settings.md) for the matching settings-file keys.

## What to do if it doesn't work

If `chimera mink` errors at startup or hangs on the first call, walk
the troubleshooting section in [`providers.md`](providers.md) — it
covers Ollama auth handshakes, missing tool capability, `num_ctx`
defaults, the `/v1` vs `/api/chat` endpoint trap, and the equivalent
Anthropic / OpenAI / Google paths.

## Troubleshooting

**`ollama signin` fails or hangs.** Check that `ollama --version` is 0.7+. Older builds shipped before cloud auth. Re-run after upgrade. If the browser flow doesn't open, run `ollama signin --help` for the device-code flow.

**`model 'kimi-k2.6:cloud' not found`.** You did not sign in, or you typed `kimi-k2.6` without the `:cloud` suffix. Re-run `ollama signin`, then `ollama run kimi-k2.6:cloud` once to confirm. The local pull (`ollama pull kimi-k2.6`) is not supported.

**First call takes 10–30 seconds.** Cold start on the cloud endpoint. Subsequent calls within 60 minutes reuse the warm instance because the provider sets `keep_alive: "60m"`.

**`tool_calls` is always empty / model never invokes a tool.** Make sure the provider is hitting `/api/chat`, not `/v1/chat/completions`. The OpenAI-compat layer silently drops `tool_calls` when streaming (Ollama issues #9632, #12557). The Chimera provider uses the native endpoint by default; if you set a custom `base_url`, it must end in the host root, not `/v1`.

**Streaming text appears but tool calls never fire on a local model.** Confirm the model has the `tools` capability. Check `ollama show <model>` — `Capabilities: tools` must be listed. `qwen3:32b`, `llama3.1:70b-instruct`, and `kimi-k2.6:cloud` all have it; many community quants do not.

**`num_ctx` defaults to 4096 and the agent forgets the system prompt.** The provider should be passing `num_ctx` per request. If you see `prompt_eval_count` capped near 4096, the request is missing `options.num_ctx`. Verify with `OLLAMA_DEBUG=1 ollama serve` and inspect the incoming JSON.

**`Tool result message rejected`.** Tool result messages must use `{"role": "tool", "tool_name": "<name>", "content": "<string>"}`. The provider builds this; if you patched `_convert_messages()`, ensure `tool_name` is present and `content` is a string (stringify JSON results).
