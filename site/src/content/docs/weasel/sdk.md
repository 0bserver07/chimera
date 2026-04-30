---
title: Weasel SDK
description: Embed weasel in a Python application — the Agent class, sync and async forms, streaming, sessions, tool registration, and a worked recipe.
---

# Weasel SDK

`chimera.weasel.sdk` is the embedded form of weasel. Use it when
you want a coding agent inside your Python application without the
subprocess + JSON-RPC overhead of the [RPC mode](modes.md). It is
the same loop, the same tool registry, the same extension surface
— exposed as a class.

## Install

```bash
uv sync --extra anthropic   # or --extra openai, etc.
```

The SDK ships with the core `chimera-run` package; no extra extra
is required.

## Hello world

Sync form:

```python
from chimera.weasel.sdk import Agent

agent = Agent(model="claude-sonnet-4-6")
result = agent.run("list the top-level files and read the README")
print(result.text)
print(f"cost: ${result.cost:.4f} across {result.steps} steps")
```

Async form:

```python
import asyncio
from chimera.weasel.sdk import Agent

async def main() -> None:
    agent = Agent(model="claude-sonnet-4-6")
    result = await agent.arun("explain this repo")
    print(result.text)

asyncio.run(main())
```

Streaming (async only — sync streaming uses a generator):

```python
import asyncio
from chimera.weasel.sdk import Agent

async def main() -> None:
    agent = Agent(model="claude-sonnet-4-6")
    async for event in agent.stream("explain this repo"):
        if event.type == "text_delta":
            print(event.text, end="", flush=True)
        elif event.type == "tool_call":
            print(f"\n[tool] {event.name}({event.args})")

asyncio.run(main())
```

Sync streaming:

```python
for event in agent.iter_stream("explain this repo"):
    if event.type == "text_delta":
        print(event.text, end="", flush=True)
```

## The `Agent` class

```python
from chimera.weasel.sdk import Agent

agent = Agent(
    model="claude-sonnet-4-6",
    cwd=".",
    max_steps=50,
    allowed_tools=None,                  # None = all built-ins
    extensions_dir=".weasel/extensions",
    no_extensions=False,
    save=True,                           # write to ~/.chimera/eventlog/
    thinking="off",                      # off|minimal|low|medium|high|max
    api_key=None,                        # falls back to env vars
    base_url=None,                       # for OpenAI-compatible endpoints
    permissions=None,                    # ConfirmationPolicy instance
    on_event=None,                       # callable(event) -> None
)
```

All keyword args mirror the CLI flags. `model=None` activates the
same auto-detection chain documented in [`providers.md`](providers.md).

### Methods

| Method | Sync / Async | Returns |
|---|---|---|
| `run(prompt: str)` | sync | `RunResult` |
| `arun(prompt: str)` | async | `RunResult` |
| `iter_stream(prompt: str)` | sync generator | `Iterator[Event]` |
| `stream(prompt: str)` | async generator | `AsyncIterator[Event]` |
| `steer(text: str)` | sync | `None` (queues a mid-turn message) |
| `cancel()` | sync | `None` (cooperative cancel) |
| `compact(strategy: str = "summary")` | sync | `int` (tokens freed) |
| `save_session(path: str)` | sync | `Path` |
| `resume(run_id: str)` | sync | `None` |
| `register_tool(tool)` | sync | `None` |
| `register_hook(event: str, fn)` | sync | `None` |

### `RunResult`

```python
@dataclass
class RunResult:
    text: str            # final assistant message
    cost: float          # USD spend on this run
    steps: int           # number of agent steps
    success: bool        # did the agent finish cleanly
    run_id: str          # eventlog id under ~/.chimera/eventlog/weasel-...
    tools_used: list[str]
    duration_seconds: float
    messages: list[dict] # full transcript (omitted if save=False)
```

### `Event`

```python
@dataclass
class Event:
    type: str            # text_delta | tool_call | tool_result | step_end | error | final
    # one of:
    text: str | None         # text_delta
    name: str | None         # tool_call / tool_result
    args: dict | None        # tool_call
    output: str | None       # tool_result
    ok: bool | None          # tool_result
    step: int | None         # step_end
    cost: float | None       # step_end / final
    error: str | None        # error
```

## Multi-turn conversations

`run()` is one turn. To carry context across turns, reuse the same
`Agent` instance:

```python
agent = Agent(model="claude-sonnet-4-6")
agent.run("list the top-level files")
agent.run("read the most interesting one")
agent.run("summarize what you found")   # remembers the previous turns
```

The full message history lives on `agent.context`; the session is
auto-compacted when it crosses the token budget.

To reset: `agent.clear()` (drops history, keeps the same provider).

## Resuming a session

```python
agent = Agent(model="claude-sonnet-4-6")
agent.resume("weasel-20260430T101455-1f3c2a8b")
agent.run("now finish the refactor we started")
```

Resume reads the event log under `~/.chimera/eventlog/weasel-<id>/`
and rehydrates the message history. Provider config in the resume
target wins unless overridden by the constructor.

## Registering tools at runtime

The same decorators that ship in [`extensions.md`](extensions.md)
work in-process:

```python
from chimera.weasel.sdk import Agent, tool

@tool
def fetch_weather(city: str) -> str:
    """Return current weather for <city>."""
    return _call_api(city)

agent = Agent(model="claude-sonnet-4-6")
agent.register_tool(fetch_weather)
agent.run("what's the weather in Lisbon?")
```

Tool functions are introspected for argument types via
`inspect.signature` + type hints; weasel generates the JSON schema
the model sees. Async tool functions are supported in the async
loop.

## Hooks at runtime

```python
agent = Agent(model="claude-sonnet-4-6")

def gate_bash(event):
    if event.tool == "bash" and "rm -rf" in event.args.get("cmd", ""):
        return {"deny": "blocked at runtime"}

agent.register_hook("pre_tool_use", gate_bash)
```

## Custom event sink

Pipe every event into your own logger / metrics:

```python
import json

def sink(event):
    print(json.dumps({"type": event.type, "step": event.step}))

agent = Agent(model="claude-sonnet-4-6", on_event=sink)
agent.run("any prompt")
```

This is the same callback shape that the CLI's `--stream-json` uses.

## Permissions in embedded mode

By default the SDK uses the project default permission policy
(allow reads, allow writes, ask for risky bash, deny destructive
patterns). To override:

```python
from chimera.permissions import AlwaysDeny, AutoApprove
from chimera.weasel.sdk import Agent

# Read-only crawl
agent = Agent(
    model="claude-sonnet-4-6",
    permissions=AlwaysDeny(scope=["bash", "write", "edit"]),
)

# Trusted CI agent
agent = Agent(model="claude-sonnet-4-6", permissions=AutoApprove())
```

The `ConfirmationPolicy` ABC is documented in
`chimera/permissions/base.py`. Mid-loop prompts are not surfaced
through the SDK; if the policy returns `ask`, the SDK treats it as
`deny` unless an `on_permission` callback is configured.

## Concurrency

`Agent` is **not** thread-safe — give each thread its own instance.
For parallel runs in async code, instantiate one `Agent` per task:

```python
import asyncio
from chimera.weasel.sdk import Agent

async def crawl(prompt: str) -> str:
    agent = Agent(model="claude-sonnet-4-6")
    result = await agent.arun(prompt)
    return result.text

async def main() -> None:
    answers = await asyncio.gather(
        crawl("summarize src/a.py"),
        crawl("summarize src/b.py"),
        crawl("summarize src/c.py"),
    )
    for a in answers:
        print(a)

asyncio.run(main())
```

Each `Agent` holds its own provider instance, its own context, its
own event log.

## Worked recipe — repository auditor

```python
import asyncio
from pathlib import Path
from chimera.weasel.sdk import Agent

PROMPT = """
Audit this repository for the following issues and return a JSON
summary with keys: missing_tests, todo_count, license_present.
"""

async def audit(repo: Path) -> dict:
    agent = Agent(model="claude-sonnet-4-6", cwd=str(repo))
    result = await agent.arun(PROMPT)
    return result  # parse JSON from result.text in real code

if __name__ == "__main__":
    audit_result = asyncio.run(audit(Path(".")))
    print(audit_result.text)
```

## See also

- [`quickstart.md`](quickstart.md) — short tour with one example each.
- [`modes.md`](modes.md) — the SDK is mode 4 of 4.
- [`extensions.md`](extensions.md) — same decorators, file-based.
- [`providers.md`](providers.md) — provider chain semantics also
  apply to embedded use.
