---
title: "Embed a coding agent"
description: "Put a working Chimera coding agent inside your own Python program in five minutes — one stable surface (AgentSession / run_agent) with streamed events, mid-turn steering, cost tracking, and clean shutdown. No TUI, no CLI."
---

Chimera's assembled coding agent is embeddable through **one documented,
stable surface**: `chimera.AgentSession` (a session your app owns) and
`chimera.run_agent` (the blocking one-liner). You do not need the TUI, the
CLI, or any knowledge of the 8-layer internals — the same seam Chimera's own
frontends run on is importable from the package root.

**Stability:** this surface is semver-stable within the 0.9.x line —
additions only, no renames, no signature breaks.

## 1. Install

```bash
pip install "chimera-run[anthropic]"
```

The PyPI package is `chimera-run`; the import name is `chimera`. The
`anthropic` extra carries the wire client used by Anthropic-protocol
endpoints — that includes GLM served via api.z.ai, which is what the
examples below use.

## 2. Credentials

Point the environment at your model endpoint. For GLM-5 via z.ai (the setup
Chimera itself is tested against):

```bash
export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
export ANTHROPIC_API_KEY="your-z.ai-key"
```

Any model id the provider factory recognizes works the same way —
`claude-sonnet-4-...` (hosted Anthropic), `gpt-...` (OpenAI), `gemini-...`
(Google), `qwen3`/`llama`/... (local Ollama, no key needed), or a
`modal-endpoint/...` model you serve yourself. The model string is the only
thing you change; see the Third-Party Providers guide on the docs site for
every backend's environment variables.

## 3. Five minutes to a working agent

Save as `embed_demo.py` and run it. It gives the agent a scratch directory,
streams every event of one real coding turn, then prints what it cost:

```python
import asyncio
import pathlib

import chimera

WORKDIR = pathlib.Path("chimera-scratch")
WORKDIR.mkdir(exist_ok=True)

TASK = "Create fib.py containing a fibonacci(n) function with a docstring."


async def main() -> None:
    with chimera.AgentSession(model="glm-5.2", project_dir=WORKDIR) as session:
        async for ev in session.send(TASK):
            line = chimera.render_event(ev)
            if line:
                end = "" if ev.type == chimera.LoopEventType.assistant_chunk else "\n"
                print(line, end=end, flush=True)
        print(f"\ncost: ${session.total_cost:.4f} across {session.turn_count} turn(s)")


asyncio.run(main())
```

You will watch the model think, call `write_file`, and finish — then find
`chimera-scratch/fib.py` on disk and the session's real dollar cost on your
terminal. `render_event` is the no-frills renderer; the typed events are
yours to render differently (below).

### The blocking one-liner

No event loop, no streaming — task in, result out:

```python
import chimera

result = chimera.run_agent(
    "Create fib.py containing a fibonacci(n) function.",
    model="glm-5.2",
    project_dir="chimera-scratch",
)
print(result.text)                       # the final assistant message
print(f"${result.cost_usd:.4f} in {result.steps} step(s), {result.reason}")
```

`run_agent` returns a `TurnResult` — `text`, `cost_usd`, `steps`, `reason`
(`"completed"`, `"max_turns"`, ...), `duration_ms`, `usage`. Inside an async
app, use `await session.run_async(task)` instead.

## 4. Next steps

### Steer it mid-turn

`send()` is an async generator, so your app stays responsive while a turn
runs — and can inject course corrections between tool calls:

```python
async def steered() -> None:
    with chimera.AgentSession(model="glm-5.2", project_dir=WORKDIR) as session:
        async def consume() -> None:
            async for ev in session.send("Refactor utils.py into a package."):
                ...  # render events

        turn = asyncio.create_task(consume())
        await asyncio.sleep(5)
        session.steer("Keep the public API identical — re-export everything.")
        await turn
```

`session.queue_follow_up(text)` instead waits until the agent would stop,
then hands it the next instruction. `session.cancel()` aborts cooperatively.

### Swap the model — including one you serve yourself

The session is model-agnostic; the string picks the backend:

```python
chimera.AgentSession(model="glm-5.2[1m]", ...)           # GLM, 1M-token window
chimera.AgentSession(model="claude-sonnet-4-20250514", ...)  # hosted Anthropic
chimera.AgentSession(model="qwen3", ...)                 # local Ollama
chimera.AgentSession(model="modal-endpoint/zai-org/GLM-5.2-FP8", ...)
```

The last line talks to an inference endpoint you deployed on Modal —
scale-to-zero GPU serving with the same session code; setup in
`docs/guides/use-modal-endpoints.md`.

### Render events yourself — a custom frontend

Every event is a `chimera.LoopEvent` with a `chimera.LoopEventType`:
`assistant_chunk` (streamed text deltas), `tool_use` / `tool_result`,
`thinking_chunk`, `compact_boundary`, `error`, and a terminal `result`
carrying cost and duration. Switch on `ev.type` and render however your UI
wants:

```python
async for ev in session.send(task):
    if ev.type == chimera.LoopEventType.tool_use:
        show_spinner(ev.data.name, ev.data.arguments)
    elif ev.type == chimera.LoopEventType.assistant_chunk:
        append_to_chat(ev.data)
```

The full event vocabulary, the double-print rule, and the path from "REPL"
to "multi-lane TUI" are in `docs/building-a-tui.md` — everything there
applies, because `AgentSession` *is* the same driver the Chimera TUIs use.

## The surface, tier by tier

| Tier | You write | You get |
|------|-----------|---------|
| One-liner | `chimera.run_agent(task, model=..., project_dir=...)` | a finished turn: `TurnResult` |
| Configured | `chimera.AgentSession(model=..., preset=..., max_turns=..., provider=..., permission_callback=...)` | streaming `send()`, blocking `run()`/`run_async()`, `steer` / `queue_follow_up` / `cancel` / `clear` / `close`, and `model` / `tools` / `total_cost` / `turn_count` / `history` / `context_window` |
| Subclassable | `class Mine(chimera.AgentSession)` — or inject `provider=` / `tools_override=` / `extra_tools=` | your behavior on the same stable contract |

Notes for embedders:

- **Working directory.** The agent's file and shell tools are rooted at
  `project_dir`. Point it at a scratch directory first; point it at a real
  repo when you mean it.
- **Approvals.** With no `permission_callback`, the embedded agent
  auto-approves its own tool calls (bypass mode). Pass a callback to gate
  risky actions through your own UI.
- **Presets.** `coding_agent` (default, full stack), `codex`, `minimal`
  (four basic tools), `explore` (read-only) — the read-only preset is the
  safe choice for untrusted tasks.
- **Cost.** `session.total_cost` accrues across turns from per-model
  pricing; unpriced models report `0.0`.
