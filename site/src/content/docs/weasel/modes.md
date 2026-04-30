---
title: Weasel Modes
description: Deep dive on the four weasel operating modes — interactive REPL, one-shot print, stdio JSON-RPC, and the embedded SDK — plus the dispatch logic that picks one.
---

# Weasel — the four modes

Weasel is a single coding agent loop with four I/O envelopes. Pick
the envelope that matches how you want to drive it; the underlying
ReAct loop, tool registry, extension surface, and event-sourced
session store are identical across all four.

This page is the long form. For the short tour see
[`quickstart.md`](quickstart.md).

## Why four modes

Different workflows want different ergonomics:

| Workflow | Wants | Mode |
|---|---|---|
| Pair-programming at the terminal | Streaming, mid-turn steering, slash commands | `interactive` |
| CI / shell pipelines / `xargs` | One-shot, deterministic stdout | `print` |
| Another tool drives the agent | Bidirectional structured channel | `rpc` |
| Embedded in a Python app | Direct API, no subprocess | `sdk` |

Weasel ships all four and refuses to favor one. The dispatch lives
in `chimera/weasel/modes.py`; the entry point is the `--mode` flag.

## Mode dispatch

`chimera/weasel/cli.py` resolves the mode in this order:

1. Explicit `--mode <interactive|print|rpc|sdk>` flag.
2. `-p / --print` flag set → `print`.
3. Stdin is a TTY and stdout is a TTY → `interactive`.
4. Stdin is a pipe and `--mode` not set → `print` (read prompt from stdin).
5. Else → `interactive` (and warn to stderr).

`sdk` is never auto-selected; it is reachable only via
`from chimera.weasel.sdk import Agent` from your own Python code.

## Mode 1 — Interactive (REPL)

Default mode. Run `chimera weasel` with no flags.

### What you get

- Streaming assistant text rendered as it arrives.
- Tool calls rendered inline as `▶ <Tool>(<args>)`.
- Mid-turn steering: type while the agent is working; the queued
  message lands at the next safe boundary.
- `Ctrl-C` cancellation: graceful first press, hard exit on second.
- Slash commands (intentionally minimal): `/help`, `/exit`,
  `/model`, `/cost`, `/clear`, `/sessions`, `/extensions`, `/compact`.
- Auto-compaction at the configured token budget.
- Event-sourced session under `~/.chimera/eventlog/weasel-<id>/`.

### What you do not get

- No `/agent` (sub-agents are out of scope; install an extension).
- No `/plan` (plan mode is out of scope; use a prompt template).
- No `/yolo` (no fine-grained approval modes; weasel ships sane
  defaults and lets the permission framework do its job).
- No theme system, no terminal-title rewriting, no IDE bridge.

If you want any of those, that's what extensions are for. See
[`extensions.md`](extensions.md).

### REPL flags

```bash
chimera weasel
chimera weasel --model gpt-4o
chimera weasel --models claude-sonnet-4-6,gpt-4o,glm-4.6  # cycle with /model
chimera weasel --no-save                                  # don't persist
chimera weasel --resume weasel-20260430T101455-1f3c2a8b   # resume a session
chimera weasel --extensions-dir ./my-exts                 # override discovery
```

## Mode 2 — Print (one-shot)

```bash
chimera weasel -p "explain this repo"
chimera weasel --print "explain this repo"   # synonym
```

Reads one prompt, runs the loop until the agent emits a final
message (or exhausts `--max-steps`), prints, exits. Designed for
pipelines:

```bash
chimera weasel -p "summarize the diff" < diff.patch
git log --oneline | chimera weasel -p "group these commits by theme"
echo "what's in pyproject.toml" | chimera weasel
```

When stdin is a pipe and no prompt arg is passed, weasel reads the
prompt from stdin.

### Output formats

| Flag | Stdout shape | Use when |
|---|---|---|
| (default) | Final text answer, plain. | Humans, pipelines that consume text. |
| `--json` | Single JSON object with `text`, `cost`, `steps`, `success`, `run_id`. | Scripts / `jq`. |
| `--stream-json` | One JSON event per line, NDJSON. | Live consumption, log shipping. |

JSON shape:

```json
{
  "run_id": "weasel-20260430T101455-1f3c2a8b",
  "model": "claude-sonnet-4-6",
  "text": "The repo is a composable coding agent framework.",
  "cost": 0.0042,
  "steps": 4,
  "success": true,
  "tools_used": ["list_files", "Read"],
  "duration_seconds": 6.3
}
```

NDJSON event types: `text_delta`, `tool_call`, `tool_result`,
`step_end`, `error`, `final`. The `final` line is always the last
line and mirrors the `--json` shape.

### One-shot flags

```bash
chimera weasel -p "fix the test"  --max-steps 30
chimera weasel -p "audit"         --allowed-tools Read,Bash
chimera weasel -p "scratch"       --no-save
chimera weasel -p "do it"         --cwd /path/to/project
chimera weasel -p "with thinking" --thinking medium
chimera weasel -p "with verbose"  --verbose          # stream events to stderr
```

Exit codes: `0` success, `1` agent reported failure, `2` config
error, `130` cancelled.

## Mode 3 — RPC (stdio JSON-RPC)

```bash
chimera weasel --mode rpc
```

Weasel reads JSON-RPC 2.0 requests on stdin and writes responses +
event notifications on stdout. The transport is line-delimited JSON
(one message per line). This is the integration point for any other
tool that wants weasel as a subprocess: an editor plugin, a TUI
front-end, a test harness, an evals runner.

### Request methods

| Method | Params | Returns |
|---|---|---|
| `prompt` | `{ "text": str, "thinking"?: str, "allowed_tools"?: [str] }` | `{ "text": str, "cost": float, "steps": int }` |
| `steer` | `{ "text": str }` | `{ "queued": true }` |
| `cancel` | `{}` | `{ "cancelled": true }` |
| `get_state` | `{}` | `{ "model": str, "messages": [...], "cost": float, "compaction_pressure": float }` |
| `compact` | `{ "strategy"?: str }` | `{ "freed_tokens": int }` |
| `list_sessions` | `{ "limit"?: int }` | `[ { "id": str, "started_at": str, "model": str } ]` |
| `resume` | `{ "id": str }` | `{ "resumed": true, "messages": int }` |

### Event notifications (server → client)

Notifications use the `event` method (no `id`):

```json
{"jsonrpc":"2.0","method":"event","params":{"type":"text_delta","text":"hello"}}
{"jsonrpc":"2.0","method":"event","params":{"type":"tool_call","name":"Read","args":{"path":"README.md"}}}
{"jsonrpc":"2.0","method":"event","params":{"type":"tool_result","ok":true,"output":"..."}}
{"jsonrpc":"2.0","method":"event","params":{"type":"step_end","step":1,"cost":0.0008}}
```

Event types mirror the `chimera.events` bus: `text_delta`,
`tool_call`, `tool_result`, `step_end`, `error`, `compaction`,
`permission`, `cost`, `final`. The full vocabulary is whatever
`chimera/events/types.py` defines; weasel does not filter.

### Worked example

Two requests, one cancellation:

```text
→ {"jsonrpc":"2.0","id":1,"method":"prompt","params":{"text":"list files"}}
← {"jsonrpc":"2.0","method":"event","params":{"type":"text_delta","text":"I'll "}}
← {"jsonrpc":"2.0","method":"event","params":{"type":"tool_call","name":"list_files"}}
← {"jsonrpc":"2.0","method":"event","params":{"type":"tool_result","ok":true,"output":"..."}}
← {"jsonrpc":"2.0","id":1,"result":{"text":"Top files: ...","cost":0.0008,"steps":2}}
→ {"jsonrpc":"2.0","id":2,"method":"prompt","params":{"text":"now read README"}}
← {"jsonrpc":"2.0","method":"event","params":{"type":"text_delta","text":"Reading…"}}
→ {"jsonrpc":"2.0","id":3,"method":"cancel","params":{}}
← {"jsonrpc":"2.0","id":3,"result":{"cancelled":true}}
← {"jsonrpc":"2.0","id":2,"error":{"code":-32099,"message":"cancelled"}}
```

### Error codes

| Code | Meaning |
|---|---|
| `-32700` | Parse error (invalid JSON). |
| `-32600` | Invalid request. |
| `-32601` | Method not found. |
| `-32602` | Invalid params. |
| `-32099` | Cancelled. |
| `-32098` | Provider error. |
| `-32097` | Permission denied. |

## Mode 4 — SDK (embedded)

```python
from chimera.weasel.sdk import Agent
```

Not a CLI mode — selected by importing. See [`sdk.md`](sdk.md) for
the full recipe; the short form:

```python
agent = Agent(model="claude-sonnet-4-6")
result = agent.run("list the top-level files")
print(result.text)
```

The SDK is the same loop. Tools, extensions, sessions, events all
behave identically to the CLI modes. Use the SDK when you want to
embed weasel in a larger Python application without paying the
subprocess + JSON-RPC tax.

## Cross-mode invariants

The following hold regardless of mode:

- **One provider per process.** To swap, exit and re-launch.
- **Extensions auto-discover.** `.weasel/extensions/*` is loaded at
  startup in every mode (CLI flag `--no-extensions` disables).
- **Sessions are event-sourced.** Every mode writes to
  `~/.chimera/eventlog/weasel-<id>/` unless `--no-save` is set.
- **The same tool registry.** No mode adds or removes built-in tools.
- **The same permission framework.** `chimera.permissions` rules
  apply to bash + write tools in every mode.

## See also

- [`quickstart.md`](quickstart.md) — short tour of all four modes.
- [`extensions.md`](extensions.md) — how to add tools / hooks / slash commands.
- [`sdk.md`](sdk.md) — embedded Agent class.
- [`providers.md`](providers.md) — provider chain.
- [`parity-matrix.md`](parity-matrix.md) — surface-by-surface mapping.
