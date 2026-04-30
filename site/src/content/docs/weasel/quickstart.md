---
title: Weasel Quickstart
description: Install weasel, learn the four operating modes, and run one example in each — interactive REPL, one-shot print, stdio RPC, and embedded SDK.
---

# `chimera weasel` Quickstart

`chimera weasel` is the fourth Chimera coding-agent CLI. Where
[`chimera mink`](../mink/quickstart.md) mirrors a TUI-first agent,
[`chimera otter`](../otter/quickstart.md) mirrors a server-first
multi-client agent, and [`chimera ferret`](../ferret/quickstart.md)
mirrors an IDE-first sandbox-first agent, weasel mirrors **the
minimal harness**: powerful defaults plus four operating modes,
adapt-to-your-workflow rather than ship-every-feature.

The headline trade is simplicity. Weasel ships **no sub-agents, no
plan mode, no built-in approval presets, no opinionated session
chrome**. What it ships is a clean four-mode entry surface, an
auto-discovered `.weasel/extensions/` directory, and an embeddable
`Agent` class. If you want more, you build it (or install an
extension); weasel will not get in the way.

This page walks the four entry points end-to-end. For deeper dives:

- [`modes.md`](modes.md) — interactive / print / rpc / sdk in detail.
- [`extensions.md`](extensions.md) — `.weasel/extensions/` layout.
- [`sdk.md`](sdk.md) — `from chimera.weasel.sdk import Agent`.
- [`providers.md`](providers.md) — provider chain.
- [`parity-matrix.md`](parity-matrix.md) — upstream surface mapping.
- [`security-and-trademarks.md`](security-and-trademarks.md) — policy.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- One of: an Anthropic API key, an OpenAI API key, an OpenRouter API
  key, or a running Ollama daemon

```bash
uv --version                          # >= 0.4
uv sync --extra dev --extra anthropic # core + Anthropic SDK
```

The Anthropic extra is recommended for the default model. To drive
weasel against OpenAI, OpenRouter, llama.cpp, or Ollama, swap the
extra (`--extra openai`) or skip it; the OpenAI-compatible adapter
is stdlib + httpx. The full matrix lives in [`providers.md`](providers.md).

## Provider configuration

Weasel resolves the provider in this order (first match wins):

1. `--model <id>` on the CLI.
2. `$WEASEL_MODEL` environment variable.
3. `$ANTHROPIC_API_KEY` set → defaults to `claude-sonnet-4-6`.
4. `$OPENAI_API_KEY` set → defaults to `gpt-4o`.
5. `$OPENROUTER_API_KEY` set → defaults to `anthropic/claude-sonnet-4`.
6. Local Ollama daemon reachable on `:11434` → first installed tag.
7. Friendly error pointing at the env vars above.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# OR
export OPENAI_API_KEY=sk-...
# OR
export OPENROUTER_API_KEY=sk-or-...
```

## The four modes at a glance

| Mode | Command | When to use |
|---|---|---|
| Interactive | `chimera weasel` | Day-to-day, conversational, mid-turn steering. |
| Print (one-shot) | `chimera weasel -p "..."` | Scripts, CI, `xargs`, shell pipelines. |
| RPC (stdio) | `chimera weasel --mode rpc` | Process integration: another tool drives weasel over JSON-RPC. |
| SDK (embedded) | `from chimera.weasel.sdk import Agent` | Drop into your Python app; no subprocess. |

The modes share **one** loop, **one** tool registry, **one**
extension surface, and **one** session store. Switching modes does
not change semantics — only the I/O envelope.

## Mode 1 — Interactive REPL

Run `chimera weasel` with no flags for a streaming REPL:

```bash
chimera weasel
```

```text
weasel · claude-sonnet-4-6 · /Users/me/proj
> list the top-level files and read the README
I'll list the repo first, then read the README.

▶ list_files(path=".")
CHANGELOG.md  CLAUDE.md  README.md  chimera/  docs/  examples/  tests/

▶ Read(path="README.md")
# Chimera
A composable coding agent framework
...

The repo root has a README pitching Chimera as a composable coding agent framework.
> ▌
```

The REPL streams assistant text + tool calls inline, accepts
mid-turn steering (just type while the agent is working), and
supports `Ctrl-C` cancellation. Slash commands are intentionally
sparse: `/help`, `/exit`, `/model`, `/cost`, `/clear`, `/sessions`,
`/extensions`. Anything else you want, you add via an extension.

Every REPL session is event-sourced under
`~/.chimera/eventlog/weasel-<utc>-<uuid>/`. To resume:

```bash
chimera weasel sessions list
chimera weasel sessions show weasel-20260430T101455-1f3c2a8b
```

## Mode 2 — Print (one-shot)

`-p` runs a single turn and exits. Plain text on stdout by default,
JSON with `--json`:

```bash
chimera weasel -p "list the top-level files and read the README"
chimera weasel -p "summarize TODO comments in src/" --json
chimera weasel -p "ship it" --max-steps 5
chimera weasel --model gpt-4o -p "draft a release note"
chimera weasel -p "audit" --allowed-tools Read,Bash --no-save
```

Stdout is the agent's final text answer (or one JSON blob with
`--json`). Stderr carries the run-id banner so pipelines can keep
stdout clean:

```bash
chimera weasel -p "what does this repo do" | tee summary.txt
```

## Mode 3 — RPC (stdio JSON-RPC)

`--mode rpc` turns weasel into a JSON-RPC 2.0 server speaking on
stdin/stdout — the integration point for outside tools that want to
drive a weasel session as a subprocess.

```bash
chimera weasel --mode rpc < requests.jsonl
```

Request:

```json
{"jsonrpc":"2.0","id":1,"method":"prompt","params":{"text":"list files"}}
```

Response stream:

```json
{"jsonrpc":"2.0","method":"event","params":{"type":"text_delta","text":"I'll "}}
{"jsonrpc":"2.0","method":"event","params":{"type":"tool_call","name":"list_files"}}
{"jsonrpc":"2.0","id":1,"result":{"text":"...","cost":0.0042}}
```

Methods: `prompt`, `steer`, `cancel`, `get_state`, `compact`,
`list_sessions`, `resume`. Full schema lives in [`modes.md`](modes.md).

## Mode 4 — SDK (embedded)

For when you want weasel inside your Python process — no subprocess,
no JSON-RPC, just a class. Both sync and async forms ship.

```python
from chimera.weasel.sdk import Agent

agent = Agent(model="claude-sonnet-4-6")
result = agent.run("list the top-level files and read the README")
print(result.text)
print(f"cost: ${result.cost:.4f}")
```

Async form:

```python
import asyncio
from chimera.weasel.sdk import Agent

async def main() -> None:
    agent = Agent(model="claude-sonnet-4-6")
    async for event in agent.stream("explain this repo"):
        if event.type == "text_delta":
            print(event.text, end="", flush=True)

asyncio.run(main())
```

Full recipe lives in [`sdk.md`](sdk.md).

## Extensions

Weasel auto-discovers `.weasel/extensions/*.{py,js,ts}` in the cwd
and `~/.weasel/extensions/` globally. Extensions register tools,
hooks, slash commands, and prompt templates. Layout:

```text
.weasel/
  settings.json
  extensions/
    my-tool.py            # Python (importlib)
    my-script.ts          # TS/JS (subprocess via Node)
    feature/
      manifest.json
      index.py
```

A minimal Python extension:

```python
from chimera.weasel.sdk import extension, tool

@extension(name="hello", version="0.1")
def register(api):
    @tool
    def hello(name: str) -> str:
        """Say hi."""
        return f"hello, {name}!"
    api.register_tool(hello)
```

Drop it under `.weasel/extensions/hello.py` and the next `chimera
weasel` invocation will pick it up. Full schema in
[`extensions.md`](extensions.md).

## Env vars at a glance

| Variable | Default | Meaning |
|---|---|---|
| `WEASEL_MODEL` | (unset) | Default model id when `--model` is not passed. |
| `ANTHROPIC_API_KEY` | (unset) | Activates the Anthropic provider chain. |
| `OPENAI_API_KEY` | (unset) | Activates the OpenAI provider chain. |
| `OPENROUTER_API_KEY` | (unset) | Activates the OpenRouter (compatible) provider chain. |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama daemon root (set for remote daemons). |
| `WEASEL_EXTENSIONS_DIR` | `.weasel/extensions/` | Override the extensions search root. |
| `NO_COLOR` | (unset) | Force the plain output handler. |

## What gets written to disk

| Path | What |
|---|---|
| `~/.chimera/eventlog/weasel-<id>/` | Per-run event stream + summary. |
| `.weasel/settings.json` | Project-local settings (model, extensions allowlist). |
| `.weasel/extensions/` | Project-local extensions. |
| `~/.weasel/extensions/` | User-global extensions. |
| `~/.chimera/credentials.json` | OAuth-issued tokens (mode `0o600`). |

Everything is local and plaintext. No remote telemetry. To purge
old runs: `rm -rf ~/.chimera/eventlog/weasel-*`.

## Where to go next

- Pick the right mode for your job: [`modes.md`](modes.md).
- Write an extension: [`extensions.md`](extensions.md).
- Embed weasel in a Python app: [`sdk.md`](sdk.md).
- Pick a provider: [`providers.md`](providers.md).
- Want the surface-by-surface parity status? [`parity-matrix.md`](parity-matrix.md).
- Trademark + security policy: [`security-and-trademarks.md`](security-and-trademarks.md).
