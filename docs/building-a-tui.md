# Building a TUI or REPL on Chimera

Chimera's coding agent is event-driven: the agent **emits** a stream of typed
events, and the UI **renders** them. A terminal REPL, a Textual TUI, a web
frontend, or an editor plugin are all just different renderers over the same
event stream. You drive everything through one object — `AgentDriver` — instead
of touching loop internals.

```python
from chimera.assembly.driver import AgentDriver, render_event

driver = AgentDriver(model="glm-5.2[1m]", project_dir=".")
async for ev in driver.send("fix the failing test in calc.py"):
    line = render_event(ev)
    if line is not None:
        print(line, end="" if ev.type.name == "assistant_chunk" else "\n")
print(f"cost: ${driver.total_cost:.4f}")
```

That's a working (if minimal) coding agent. The reference REPL
(`chimera code`) is ~70 lines on top of this.

## AgentDriver

`chimera.assembly.driver.AgentDriver` — a small, stateful control surface.

### Construction

```python
AgentDriver(
    model="glm-5.2",          # any provider id; "[1m]" etc. sets the context window
    project_dir=".",          # the directory the agent's tools operate in
    preset="coding_agent",    # coding_agent | codex | minimal | explore
    *,
    interactive=True,         # True: disable autonomous nudges (see below)
    **agent_kwargs,           # forwarded to CodingAgent (max_turns, provider, ...)
)
```

`interactive=True` (the default) turns **off** the autonomous "you didn't use
any tools / keep going" nudges. Those help unattended `-p`/benchmark runs finish
on their own, but in an interactive session they make conversational turns
("what does this function do?") ramble. Set `interactive=False` for unattended
drivers that should push themselves to completion.

### Driving a turn

| Call | Effect |
|------|--------|
| `async for ev in driver.send(text)` | Run one turn; yield events as they happen. Conversation history and cost accrue automatically. |
| `driver.steer(text)` | Inject a message **mid-run**, delivered between tool turns. Thread-safe enough to call from an input thread while `send()` streams. |
| `driver.queue_follow_up(text)` | Queue a message delivered after the agent would otherwise stop. |
| `driver.cancel()` | Cooperatively abort the current turn (takes effect at the next step). |
| `driver.clear()` | Forget the conversation; the next `send()` starts fresh. |

### State

| Property | Meaning |
|----------|---------|
| `driver.model` | Wire model id (e.g. `glm-5.2`). |
| `driver.context_window` | Token window (e.g. `1_000_000` for `glm-5.2[1m]`), or `None`. |
| `driver.tools` | The agent's tool instances. |
| `driver.total_cost` | Cumulative USD across all turns this session. |
| `driver.turn_count` | Number of completed turns. |
| `driver.history` | Accumulated conversation messages (persisted across `send()`). |
| `driver.agent` | The underlying `CodingAgent`, for advanced access. |

## The event vocabulary

Every event is a `chimera.core.loop_events.LoopEvent` with `.type`
(`LoopEventType`), `.data`, `.turn`, and `.timestamp`.

| `LoopEventType` | `.data` is… | Render as |
|-----------------|-------------|-----------|
| `assistant_chunk` | a text delta (`str`) | stream it inline (`print(end="")`) |
| `assistant` | the full `Response` | **skip if you streamed chunks** (avoids double-print); else show `.content` |
| `tool_use` | a `ToolCall` (`.name`, `.arguments`) | "⚙ calling `name(args)`" — shown **before** the result |
| `tool_result` | `(ToolCall, ToolResult)` | the result; `.output`, `.success` |
| `tool_progress` | partial tool output | optional live progress |
| `compact_boundary` | `"auto_compact"` | a subtle "context compacted" marker |
| `system` | a `str` (e.g. slash-command output) | print it |
| `error` | an error/message | show as an error |
| `result` | a `LoopResult` (`.reason`, `.cost_usd`, `.turn_count`, `.messages`) | end-of-turn footer (cost/steps) |

`render_event(ev)` gives you a no-frills single-line rendering (returns `None`
to skip). Use it for a quick REPL, or read the typed events and render them
however your UI wants — colors, collapsible tool calls, live diffs, etc.

### Double-print rule

The agent emits **both** `assistant_chunk` deltas (while streaming) **and** a
final `assistant` message with the full text. Render one or the other, never
both. The reference REPL tracks `saw_chunk` and only prints the final
`assistant` when nothing streamed (non-streaming presets).

## Mid-run steering

`send()` is an async generator, so a TUI can keep an input widget live while a
turn streams and call `driver.steer(text)` when the user submits. The message is
picked up between tool turns — the agent finishes its current tool call, then
sees the steer. `queue_follow_up()` instead waits until the agent would stop,
then hands it the next instruction. This is how you build "type while it works"
UX (à la a real coding TUI) without blocking the stream.

## Notes

- **Working directory.** The agent's file/shell tools are rooted at
  `project_dir`. Point the driver at a repo and the agent reads/edits there.
- **Unlimited runs.** Pass `max_turns=None` (or the CLI `--max-turns 0`) to run
  until the task completes; compaction tracks the model's real context window so
  long sessions don't overflow.
- **Cost.** Accrued from each turn's `result` event using the provider's
  pricing; unknown models report `0.0`.
