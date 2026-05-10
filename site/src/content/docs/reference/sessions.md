---
title: "chimera.sessions"
description: "Reference for chimera.sessions — Session, SessionTree, storage backends, event-sourced persistence."
---

`chimera.sessions` keeps multi-turn state across runs: conversation
history, branching trees, and durable event logs.

For a narrative walk-through, see [modules/sessions](/modules/sessions/).
This page is the canonical export list.

## Top-level exports

```python
from chimera.sessions import Session, SessionTree
from chimera.sessions.storage import (
    MemorySessionStorage,
    FileSessionStorage,
    SQLiteSessionStorage,
)
from chimera.sessions.eventlog import EventLog
```

## `Session` (`chimera.sessions.session`)

A `Session` wraps an `Agent` and exposes a chat-style API plus
checkpoint, fork, and steering primitives.

| Method | Purpose |
|---|---|
| `chat(message)` | Send a single user message and return the agent's reply. |
| `iter_chat(message)` | Async generator yielding `LoopEvent`s as the turn streams. |
| `fork(name=None)` | Branch the conversation. Returns a new child `Session`. |
| `save(path=None)` | Persist to the configured storage. |
| `resume(session_id)` | Reload an earlier session by id. |
| `steer(message)` | Inject a steering message mid-turn (consumed at the next step boundary). |
| `queue(message)` | Queue a follow-up turn that fires after the current one ends. |
| `cancel()` | Cooperatively cancel the running turn. |
| `compact()` | Run the configured `CompactionStrategy` immediately. |

Sessions auto-compact when token budgets cross thresholds and auto-save
to `~/.chimera/sessions/` after every turn (when running through the
REPL).

## `SessionTree` (`chimera.sessions.tree`)

`SessionTree` manages a graph of related sessions: parent / child links,
named branches, and in-place branch switching.

| Method | Purpose |
|---|---|
| `fork(parent_id, name=None)` | Branch from `parent_id`; returns the new node. |
| `switch(name)` | Activate an existing named branch. |
| `current()` | Get the currently-active session id. |
| `list_branches()` | Enumerate all named branches. |
| `iter_history(session_id)` | Walk parents back to the root. |

Backed by a JSONL log with file locking; thread-safe for the REPL's
threaded agent loop.

## Storage backends (`chimera.sessions.storage`)

| Backend | Use |
|---|---|
| `MemorySessionStorage` | In-process; lost when the process exits. Good for tests. |
| `FileSessionStorage` | One JSON file per session. Default for the REPL. |
| `SQLiteSessionStorage` | Indexed; supports queries by branch, time, cost. |

All backends implement the same `SessionStorage` ABC: `save(session)`,
`load(session_id)`, `list_sessions()`, `delete(session_id)`.

## Event-sourced persistence (`chimera.sessions.eventlog`)

`EventLog` is an append-only log of every `Event` emitted by the loop
(tool calls, results, steering, cancellation, etc.). On restart, the log
replays into a fresh `Session` so crashes mid-turn can be recovered to
the last consistent step. Features:

- Append-only file with locking (no mid-write corruption).
- Gap detection: missing sequence numbers raise on replay.
- Replay-on-load: reconstruct state from the log alone.

## See also

- [`chimera.events`](/reference/events/) for the event types persisted.
- [`chimera.compaction`](/reference/compaction/) for the strategies that
  rewrite a session's history when token budgets fill.
- [modules/session-tree](/modules/session-tree/) for branching semantics.
