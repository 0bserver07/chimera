---
title: Weasel Quickstart
description: Install weasel, walk all four operating modes side-by-side, write a JS/TS extension, drive --thinking + --stream-json + multi -p + @file expansion.
---

# `chimera weasel` Quickstart

`chimera weasel` is the fourth Chimera coding-agent CLI. Where
[`chimera mink`](../mink/quickstart.md) mirrors a TUI-first agent,
[`chimera otter`](../otter/quickstart.md) mirrors a server-first
multi-client agent, and [`chimera ferret`](../ferret/quickstart.md)
mirrors an IDE-first sandbox-first agent, weasel mirrors **the
minimal harness**: powerful defaults plus four operating modes —
interactive, print, rpc, sdk — and an auto-discovered
`.weasel/extensions/` directory. Adapt-to-your-workflow rather than
ship-every-feature.

Headline trade is simplicity. Weasel ships **no sub-agents, no plan
mode, no built-in approval presets, no opinionated session chrome.**
What it ships is a clean four-mode entry surface, auto-discovered
extensions, and an embeddable `Agent` class. If you want more, you
build it (or install an extension); weasel will not get in the way.

Deeper dives:

- [`modes.md`](modes.md) — interactive / print / rpc / sdk in detail.
- [`extensions.md`](extensions.md) — `.weasel/extensions/` layout.
- [`sdk.md`](sdk.md) — `from chimera.weasel.sdk import Agent`.
- [`providers.md`](providers.md) — provider chain.
- [`parity-matrix.md`](parity-matrix.md) — upstream surface mapping.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- For the JS/TS extension path: Node 20+
- One of: an Anthropic key, an OpenAI key, an OpenRouter key, an
  Ollama daemon, or an Ollama Cloud account

```bash
uv --version                          # >= 0.4
uv sync --extra dev --extra anthropic # core + Anthropic SDK
```

## Provider configuration

Weasel resolves the provider in this order (first match wins):

1. `--model <id>` on the CLI.
2. `$WEASEL_MODEL` environment variable.
3. `$ANTHROPIC_API_KEY` → `claude-sonnet-4-6`.
4. `$OPENAI_API_KEY` → `gpt-4o`.
5. `$OPENROUTER_API_KEY` → `anthropic/claude-sonnet-4`.
6. `$OLLAMA_API_KEY` → `gpt-oss:120b-cloud`.
7. Local Ollama daemon on `:11434` → first installed tag.
8. Friendly error pointing at the env vars above.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# OR — free path
export OLLAMA_HOST=https://ollama.com
export OLLAMA_API_KEY=<your-key>
```

## The four modes side-by-side

| Mode | Command | I/O envelope | When to use |
|---|---|---|---|
| Interactive | `chimera weasel` | streaming TTY | Day-to-day, conversational, mid-turn steering. |
| Print (one-shot) | `chimera weasel -p "..."` | stdout text or JSON | Scripts, CI, `xargs`, shell pipelines. |
| RPC (stdio) | `chimera weasel --mode rpc` | JSON-RPC 2.0 on stdin/stdout | Another tool drives weasel as a subprocess. |
| SDK (embedded) | `from chimera.weasel.sdk import Agent` | Python object | Drop into your Python app; no subprocess. |

The modes share **one** loop, **one** tool registry, **one** extension
surface, and **one** session store. Switching modes does not change
semantics — only the I/O envelope.

## Mode 1 — Interactive REPL

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
> ▌
```

Slash commands are intentionally sparse: `/help`, `/exit`, `/model`,
`/cost`, `/clear`, `/sessions`, `/extensions`. Anything else you want,
you add via an extension.

## Mode 2 — Print (one-shot)

`-p` runs a single turn and exits. Plain text on stdout by default,
JSON with `--json`:

```bash
chimera weasel -p "summarize TODO comments in src/" --json
chimera weasel -p "ship it" --max-steps 5
chimera weasel --model gpt-oss:120b-cloud -p "draft a release note"
chimera weasel -p "audit" --allowed-tools Read,Bash
```

### Multi `-p` (chained prompts)

Pass `-p` repeatedly to chain turns in a single non-interactive
invocation. Each `-p` reuses the same loop / context.

```bash
chimera weasel -p "read CHANGELOG.md" \
               -p "summarize the last release in 3 bullets" \
               -p "draft a tweet for it"
```

### `@file` expansion

Any token of the form `@<path>` in a `-p` argument is expanded to the
contents of that file (UTF-8, max 1 MB) inline:

```bash
chimera weasel -p "review this diff: @./pending.diff"
chimera weasel -p "rewrite @./prompt.txt to be terser"
```

### `--thinking` + `--stream-json`

Surface the model's thinking trace alongside text deltas:

```bash
chimera weasel --thinking medium --stream-json \
               -p "explain why the test is flaking" \
   | jq 'select(.event=="thinking_delta" or .event=="text_delta")'
```

Sample stream:

```json
{"event":"thinking_delta","text":"The test asserts ..."}
{"event":"text_delta","text":"Looking at the test, "}
{"event":"thinking_delta","text":"... but the fixture seeds RNG."}
{"event":"text_delta","text":"the race comes from the seeded RNG ..."}
```

Stdout is one JSON line per `LoopEvent`. Stderr carries the run-id
banner so pipelines stay clean.

## Mode 3 — RPC (stdio JSON-RPC)

`--mode rpc` turns weasel into a JSON-RPC 2.0 server on stdin/stdout:

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
`list_sessions`, `resume`. Schema in [`modes.md`](modes.md).

## Mode 4 — SDK (embedded)

For when you want weasel inside your Python process — no subprocess,
no JSON-RPC, just a class. Sync and async forms ship:

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

Full recipe in [`sdk.md`](sdk.md).

## Extensions

Weasel auto-discovers `.weasel/extensions/*.{py,js,ts}` in cwd and
`~/.weasel/extensions/` globally.

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

A minimal TypeScript extension (run via the bundled Node executor):

```typescript
// .weasel/extensions/word_count.ts
export const manifest = {
  name: "word_count",
  version: "0.1",
};

export function register(api: any) {
  api.register_tool({
    name: "word_count",
    description: "Return the word count of a file.",
    parameters: { path: "string" },
    async run({ path }: { path: string }) {
      const fs = await import("node:fs/promises");
      const text = await fs.readFile(path, "utf-8");
      return { words: text.split(/\s+/).filter(Boolean).length };
    },
  });
}
```

Drop the `.ts` file under `.weasel/extensions/word_count.ts` and the
next `chimera weasel` invocation will pick it up, transparently
shelling out to the bundled Node executor. JS works the same way; the
`manifest.name` matches the directory or filename.

## Sessions / persistence

```bash
chimera weasel sessions list
chimera weasel sessions show weasel-20260514T101455-1f3c2a8b
chimera weasel --resume weasel-20260514T101455-1f3c2a8b   # explicit
chimera weasel -c                                         # newest in cwd
```

## Choose your model

Recommended models for the minimal-harness posture:

| Backend | Tag | Why for weasel |
|---|---|---|
| Anthropic | `claude-sonnet-4-6` | Default; strongest tool calling. |
| Ollama Cloud | `gpt-oss:120b-cloud` | Free w/ Ollama account; native tools. |
| OpenAI | `gpt-4o` | Strong baseline; `$OPENAI_API_KEY`. |
| OpenRouter | `anthropic/claude-sonnet-4` | Same Anthropic model via OpenRouter. |

See [the Ollama Cloud recipe](../use-with-ollama.md).

## Env vars at a glance

| Variable | Default | Meaning |
|---|---|---|
| `WEASEL_MODEL` | (unset) | Default model id. |
| `ANTHROPIC_API_KEY` | (unset) | Anthropic chain. |
| `OPENAI_API_KEY` | (unset) | OpenAI chain. |
| `OPENROUTER_API_KEY` | (unset) | OpenRouter chain. |
| `OLLAMA_API_KEY` | (unset) | Ollama Cloud. |
| `OLLAMA_HOST` | `http://localhost:11434` | Daemon URL. |
| `WEASEL_EXTENSIONS_DIR` | `.weasel/extensions/` | Extensions search root. |
| `NO_COLOR` | (unset) | Plain output handler. |

## What gets written to disk

| Path | What |
|---|---|
| `~/.chimera/eventlog/weasel-<id>/` | Per-run event stream + summary. |
| `.weasel/settings.json` | Project-local settings. |
| `.weasel/extensions/` | Project-local extensions. |
| `~/.weasel/extensions/` | User-global extensions. |
| `~/.chimera/credentials.json` | OAuth tokens (mode `0o600`). |

Everything is local. Purge with `rm -rf ~/.chimera/eventlog/weasel-*`.

## Where to go next

- [Modes](./modes.md) — pick the right mode for the job.
- [Extensions](./extensions.md) — Python and TS/JS recipes.
- [SDK](./sdk.md) — embed weasel.
- [Providers](./providers.md).
- [Parity Matrix](./parity-matrix.md).
- [Security and Trademarks](./security-and-trademarks.md).

---

### Verified (2026-05-14)

Two commands from this quickstart, against Ollama Cloud:

```text
$ OLLAMA_HOST=https://ollama.com OLLAMA_API_KEY=*** \
    chimera weasel -p "Hello, please reply with one word: hello" \
                   --model gpt-oss:120b-cloud --max-steps 2
hello

$ chimera weasel --version
chimera weasel 0.7.0
```
