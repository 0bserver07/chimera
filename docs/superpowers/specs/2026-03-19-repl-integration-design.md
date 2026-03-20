# REPL Integration — Wire All Pi-Mono Features + Two-Mode Terminal

**Date:** 2026-03-19
**Status:** Final
**Depends on:** pi-mono adoption (all 7 features merged)

## Overview

Wire all 7 pi-mono features into `chimera code` REPL and add a two-mode terminal that enables mid-turn steering. The REPL switches between readline mode (idle) and raw stdin mode (agent running) to avoid terminal corruption.

---

## Feature 1: Two-Mode Terminal

### Problem

The current REPL blocks on `input()` → `drain_steps()` → `input()`. No way to type while the agent runs. Threading alone won't work because readline and concurrent stdout from the agent thread corrupt the terminal.

### Design

Two terminal modes:

**IDLE MODE** — readline active, full line editing, tab completion, history. Normal `> ` prompt. User types a message or slash command.

**RUNNING MODE** — readline disabled, raw stdin via `select.select()`. Agent output streams freely to stdout. User can type a line to steer, or Ctrl+C to cancel.

```
IDLE ──(user types message)──→ RUNNING
  │                               │
  │                               ├──(user types line)──→ steer(), stay RUNNING
  │                               ├──(Ctrl+C)──→ cancel(), stay RUNNING until agent stops
  │                               └──(agent finishes)──→ show result, back to IDLE
  │
  └──(/slash command)──→ handle, stay IDLE
```

### Implementation

New helper functions in `chimera/cli/code.py`:

```python
import select
import signal
import threading

def _read_steering_input() -> str | None:
    """Non-blocking read from stdin. Returns line or None."""
    readable, _, _ = select.select([sys.stdin], [], [], 0.1)
    if readable:
        line = sys.stdin.readline()
        return line.strip() if line else None
    return None
```

The main loop becomes:

```python
while True:
    # IDLE MODE — readline active
    try:
        user_input = input("\n> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nBye!")
        break

    if not user_input:
        continue
    if user_input.startswith("/"):
        _dispatch_command(user_input, session, env, print)
        continue

    # RUNNING MODE — start agent, disable readline
    cancel_token = CancellationToken()
    config.cancellation = cancel_token
    agent_result_box = [None]  # mutable box for thread result

    def _run():
        try:
            agent_result_box[0] = drain_steps(session.iter_chat(user_input))
        except Exception as e:
            agent_result_box[0] = e

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    # Poll for steering input while agent runs
    try:
        while thread.is_alive():
            line = _read_steering_input()
            if line:
                queues.steer(Message.user(line))
                print("  (steering sent)")
    except KeyboardInterrupt:
        cancel_token.cancel()
        print("\n  (cancelling...)")
        thread.join(timeout=10)

    thread.join(timeout=1)

    # Show result
    result = agent_result_box[0]
    if isinstance(result, Exception):
        print(f"\n  Error: {result}")
    elif result:
        total_cost += result.cost
        print(f"\n  [cost: ${result.cost:.4f} | steps: {result.steps}]")
```

### Platform Notes

- **macOS/Linux**: `select.select([sys.stdin], ...)` works on stdin file descriptor.
- **Windows**: `select` on stdin is not supported. Fallback: no steering, just cancellation via Ctrl+C. Agent thread runs, main thread does `thread.join()` with periodic `KeyboardInterrupt` checks.

---

## Feature 2: Wire LoopConfig with All Features

### Current State

```python
config = LoopConfig(handler=handler, cost_tracker=cost_tracker)
```

### New State

```python
from chimera.core.cancellation import CancellationToken
from chimera.core.file_tracker import FileTracker
from chimera.core.message_queue import MessageQueues

file_tracker = FileTracker()
queues = MessageQueues()

config = LoopConfig(
    handler=handler,
    cost_tracker=cost_tracker,
    file_tracker=file_tracker,
    message_queues=queues,
    # cancellation is set per-turn, not here
)
```

The `cancellation` field is set per-turn (new token each turn) to avoid stale cancelled state.

---

## Feature 3: Session Tree Auto-Save

### Design

Sessions auto-persist to `~/.chimera/sessions/<workdir-hash>.jsonl`. Every message is written immediately via `SessionTree._append_to_file()`. On next `chimera code` in the same directory, the tree is loaded and context rebuilt.

```python
import hashlib
from pathlib import Path
from chimera.sessions.tree import SessionTree

def _session_path(workdir: str) -> Path:
    h = hashlib.sha256(workdir.encode()).hexdigest()[:12]
    return Path.home() / ".chimera" / "sessions" / f"{h}.jsonl"

session_file = _session_path(workdir)
tree = SessionTree(session_file)

session = Session(agent=agent, env=env, tree=tree)
```

### Thread Safety Fix

Add a `threading.Lock` to `SessionTree.append()`:

```python
class SessionTree:
    def __init__(self, path):
        # ... existing ...
        self._lock = threading.Lock()

    def append(self, entry):
        with self._lock:
            self._entries.append(entry)
            self._by_id[entry.id] = entry
            self._children.setdefault(entry.parent_id, []).append(entry.id)
            self._active_leaf = entry.id
            self._append_to_file(entry)
```

### Session Commands

- `/session new` — close current tree, create a new session file
- `/session resume` — list available sessions, pick one to resume
- Existing `/tree`, `/branch`, `/switch` already work

---

## Feature 4: Stream-Level Cancellation

### Problem

Ctrl+C calls `cancel_token.cancel()` but the agent is mid-HTTP-request. The cancellation check only fires between steps, so the user waits up to 30+ seconds.

### Fix

Check cancellation inside the stream accumulation loop in `chimera/core/loop.py`:

```python
# In iter_steps(), inside the streaming/complete block:
# After receiving each chunk from the provider, check cancellation
if self.config and self.config.cancellation and self.config.cancellation.is_cancelled:
    break
```

This provides sub-second cancellation during streaming. For non-streaming `complete()` calls, the cancellation still fires at the step boundary (acceptable since complete() calls are short).

---

## Feature 5: File Tracker → System Prompt

### Design

After compaction or when the file tracker has data, append a section to the system prompt so the agent knows what files it's been working with:

```python
# In run_code(), after building the system prompt:
# Check periodically (e.g., before each turn)
if file_tracker.to_prompt_section():
    # Inject as a user message at turn start
    session._context.add(Message.system(file_tracker.to_prompt_section()))
```

Actually, simpler: the file tracker section is included in compaction summaries via `FileAwareCompaction`. For non-compacted sessions, the full message history already contains the tool calls that read/wrote files. No extra injection needed — the tracker is for compaction awareness.

---

## Feature 6: `--mode` CLI Flag

### Design

Add to `chimera/cli/main.py` code_parser:

```python
code_parser.add_argument(
    "--mode",
    choices=["interactive", "rpc", "json"],
    default="interactive",
    help="Output mode (default: interactive)",
)
```

In `run_code()`, dispatch early:

```python
def run_code(args):
    mode = getattr(args, "mode", "interactive")
    if mode == "rpc":
        return _run_rpc_mode(args)
    if mode == "json":
        return _run_json_mode(args)
    # ... existing interactive REPL ...
```

`_run_rpc_mode` uses the existing `RpcServer` + `RpcHandler`. It wires all features (file tracker, cancel, queues, tree) into the session, then runs `server.run()`.

---

## Files Changed

| File | Change |
|------|--------|
| `chimera/cli/code.py` | Rewrite `run_code()`: two-mode terminal, wire all features, add `_run_rpc_mode()`, `_run_json_mode()`, `_session_path()`, `_read_steering_input()` |
| `chimera/cli/main.py` | Add `--mode` arg to code subparser |
| `chimera/sessions/tree.py` | Add `threading.Lock` to `append()` |
| `chimera/core/loop.py` | Add cancellation check inside stream accumulation |
| `tests/test_cli_integration.py` | New — test feature wiring, session tree auto-save, `--mode` dispatch |

## Backward Compatibility

- Default behavior (`chimera code` with no flags) is unchanged in terms of output
- Session tree auto-save is new but transparent (writes to `~/.chimera/sessions/`)
- Steering is new capability (type while agent runs)
- Ctrl+C now cancels cleanly instead of printing `(interrupted)` — better behavior
- `--mode rpc` and `--mode json` are new flags, no effect on existing usage
