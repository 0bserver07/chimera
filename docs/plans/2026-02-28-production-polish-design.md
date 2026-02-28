# Chimera Production Polish Design

**Goal:** Complete the framework with full async support, true parallel execution, production-grade MCP/LSP, cost controls, and a full-featured REPL.

**Architecture:** 6 independent components that build on the existing stack. Async is the foundation — Ensemble and REPL benefit from it. MCP, LSP, and Cost are standalone improvements.

**Tech Stack:** Python 3.11+, stdlib only (`asyncio`, `threading`, `readline`, `queue`, `collections.deque`)

---

## Context

Chimera has 1094 passing tests. The serving layer is complete: streaming merged into ReAct, eval CLI wired, code REPL working. Six areas need production polish:

1. **Async** — `async_run()` exists but tools run sequentially, no `async_iter_steps()`, no concurrent tool execution.
2. **Parallel Ensemble** — ThreadPoolExecutor works but no async path, no early cancellation, GitEnvironment can't clone.
3. **MCP** — Client works but no retry, no stderr reading, no health checks, no tool refresh.
4. **LSP** — 4 methods work but diagnostics are broken (async notifications never read), missing workspace/symbol, code actions, completion.
5. **Cost** — `calculate_cost()` works but no budgets, no tracking, no estimation.
6. **REPL** — Functional but no readline, no slash commands, no session persistence.

---

## Component 1: Full Async Overhaul

### Problem

`async_run()` exists but:
- Tool execution is synchronous (blocks the event loop)
- No `async_iter_steps()` generator (can't yield intermediate results)
- No concurrent tool execution (3 tool calls in one response run sequentially)
- No streaming handler integration in async path

### Solution

Three layers, bottom-up:

**1a. `BaseTool.async_execute()`** (`chimera/core/tool.py`)

Default wraps sync `execute()` via `run_in_executor()`. Tool authors override for native async.

```python
async def async_execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, self.execute, args, env)
```

**1b. `async_execute_tool_calls()`** (`chimera/core/tool_executor.py`)

New async function. Runs permission checks synchronously (instant), then executes all approved tool calls concurrently via `asyncio.gather()`. Adds results to context in order.

```python
async def async_execute_tool_calls(
    tool_calls: list[ToolCall],
    tool_map: dict[str, BaseTool],
    context: Context,
    env: Environment | None,
    config: LoopConfig | None,
) -> ToolExecutionResult:
```

Key design decisions:
- Permission checks are sync (they're in-memory policy lookups, no I/O)
- Detection checks are sync (comparing strings, no I/O)
- Event bus publish is sync (fire-and-forget)
- Only tool execution itself is concurrent
- Results are ordered to match tool_calls order (not completion order)
- `LoopBreak` still raised for loop detection

**1c. `ReAct.async_iter_steps()`** (`chimera/core/loop.py`)

Async generator mirroring `iter_steps()`. Uses `provider.async_complete()` or `provider.async_stream()` depending on handler. Calls `async_execute_tool_calls()` for concurrent tool execution. Yields `StepResult` progressively.

```python
async def async_iter_steps(
    self,
    provider: Provider,
    tools: list[BaseTool],
    context: Context,
    env: Environment | None,
) -> AsyncGenerator[StepResult, None]:
```

Returns `AgentResult` via `StopAsyncIteration` (same pattern as sync `iter_steps` using `StopIteration.value`).

Existing `async_run()` is rewritten to consume `async_iter_steps()` via a new `async_drain_steps()` helper.

Add `_async_accumulate_stream()` static method (async version of `_accumulate_stream`, consumes `AsyncIterator[StreamEvent]`).

### Behavior

- `LoopConfig.handler = None`: uses `provider.async_complete()`, concurrent tool execution
- `LoopConfig.handler = ConsoleStreamHandler()`: uses `provider.async_stream()` + `_async_accumulate_stream()`, handler callbacks remain sync (print is fast)
- Backward compatible: existing sync `iter_steps()` / `run()` unchanged

---

## Component 2: Parallel Ensemble

### Problem

`Ensemble` uses ThreadPoolExecutor (works), but:
- No async path (can't use `asyncio.gather()`)
- No early cancellation when first agent succeeds
- GitEnvironment doesn't implement `clone()`

### Solution

**2a. `Ensemble.async_run()`** (`chimera/composition/ensemble.py`)

Uses `asyncio.gather()` with cloned environments. Each agent runs via `agent.loop.async_run()`. Falls back to `_run_sequential()` if `clone()` unavailable.

```python
async def async_run(self, task: str, env: Environment | None) -> list[AgentResult]:
```

**2b. Early cancellation**

New `first_success: bool = False` parameter on `__init__`. When True:
- Thread path: checks results as futures complete, cancels remaining on first success
- Async path: uses `asyncio.wait(return_when=FIRST_COMPLETED)` loop, cancels tasks on first success

**2c. `GitEnvironment.clone()`** (`chimera/env/git.py`)

Copy working tree to temp dir, init fresh git repo. Simpler than true worktree (avoids git lock conflicts).

```python
def clone(self) -> GitEnvironment:
    clone_dir = Path(tempfile.mkdtemp(prefix="chimera-git-clone-"))
    shutil.copytree(self.workdir, clone_dir, dirs_exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(clone_dir), capture_output=True)
    cloned = GitEnvironment(workdir=str(clone_dir), ...)
    cloned.setup()
    return cloned
```

DockerEnvironment.clone() is out of scope — container orchestration is too complex for the payoff.

---

## Component 3: MCP Robustness

### Problem

MCP client works but isn't production-grade:
- No retry on transport errors
- StdioTransport never reads stderr (subprocess can block)
- No health checks or reconnection
- Tool list is static after `connect_all()`

### Solution

**3a. Retry with backoff** (`chimera/mcp/client.py`)

Wrap transport `send()` calls in `call_tool()` with retry logic. 3 attempts, exponential backoff (1s, 2s, 4s). Only retries transport errors (ConnectionError, TimeoutError, OSError), not tool-level errors.

**3b. Stderr reader thread** (`chimera/mcp/transport.py`)

StdioTransport adds a daemon thread in `start()` that reads stderr into a bounded `collections.deque(maxlen=100)`. Prevents subprocess blocking. Expose via `stderr_lines` property.

**3c. Connection health check** (`chimera/mcp/client.py`)

```python
def ping(self, name: str) -> bool:
    """Send MCP ping, return True if server responds."""

def is_connected(self, name: str) -> bool:
    """Check if transport is alive."""
```

**3d. Tool refresh** (`chimera/mcp/client.py`)

```python
def refresh_tools(self, name: str | None = None) -> None:
    """Re-discover tools from one or all servers."""
```

Re-runs `tools/list` and updates `_tool_defs`. If `name` is None, refreshes all servers.

---

## Component 4: LSP Fix + Expand

### Problem

Diagnostics are fundamentally broken — LSP servers push `textDocument/publishDiagnostics` as async notifications, but `_read()` only expects one response per request. Notifications are silently dropped.

Also missing: workspace/symbol search, code actions, completion.

### Solution

**4a. Background notification reader** (`chimera/lsp/session.py`)

Rewrite the I/O model:

Current: `_send_request()` → `_write()` → `_read()` (blocking, one response)

New: Background daemon thread reads stdout continuously. Routes:
- Responses (have `id` field) → per-request `queue.Queue` in `_pending_responses: dict[int, queue.Queue]`
- Notifications (no `id`, have `method`) → handler dispatch, specifically `textDocument/publishDiagnostics` → stored in `_diagnostics: dict[str, list[Diagnostic]]` keyed by URI

`_send_request()` creates a queue for the request ID, writes the message, then waits on the queue with timeout.

```python
# New internal state in LSPSession.__init__:
self._pending_responses: dict[int, queue.Queue] = {}
self._diagnostics: dict[str, list[Diagnostic]] = {}  # uri -> latest diagnostics
self._reader_thread: threading.Thread | None = None
self._running = False
```

This is the most invasive change — it touches `start()`, `stop()`, `_send_request()`, `_read()` (removed, replaced by `_reader_loop()`). But it's the only way to receive async server notifications.

**4b. New LSP methods** (`chimera/lsp/session.py`)

```python
def workspace_symbols(self, query: str) -> list[dict[str, Any]]:
    """Search symbols across the workspace."""
    return self._send_request("workspace/symbol", {"query": query}) or []

def code_actions(self, uri: str, start_line: int, start_char: int,
                 end_line: int, end_char: int) -> list[dict[str, Any]]:
    """Get available code actions for a range."""

def completion(self, uri: str, line: int, character: int) -> list[dict[str, Any]]:
    """Get completion items at a position."""
```

**4c. Expand LSPTool** (`chimera/lsp/tool.py`)

Add `workspace_symbols`, `code_actions`, `completion` to action enum. Update parameter schema to include `query` (for workspace_symbols) and range params (for code_actions).

**4d. `LSPManager.get_diagnostics()` fixed** (`chimera/lsp/manager.py`)

Currently returns empty list. Now delegates to session's cached `_diagnostics` dict.

---

## Component 5: Cost Forecasting

### Problem

`calculate_cost()` works per-response but no cumulative tracking, budgets, or estimation.

### Solution

**5a. `CostTracker` class** (new: `chimera/providers/cost_tracker.py`)

```python
class CostLimitExceeded(Exception):
    """Raised when cost budget is exceeded."""

class CostTracker:
    def __init__(self, budget: float | None = None) -> None:
        self._total = 0.0
        self._budget = budget
        self._by_model: dict[str, float] = {}

    def record(self, cost: float, model: str = "") -> None:
        """Record a cost. Raises CostLimitExceeded if budget exceeded."""

    @property
    def total(self) -> float: ...

    @property
    def remaining(self) -> float | None: ...

    def breakdown(self) -> dict[str, float]: ...

    def reset(self) -> None: ...
```

**5b. Wire into LoopConfig** (`chimera/core/loop_config.py`)

Add `cost_tracker: CostTracker | None = None` field. ReAct checks `cost_tracker.record()` after each step — if `CostLimitExceeded`, returns `AgentResult(success=False, error="Cost limit exceeded")`.

**5c. `estimate_cost()`** (`chimera/providers/cost.py`)

```python
def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Pre-flight cost estimation. Returns 0.0 for unknown models."""
```

Simple multiplication using PRICING table. No API call needed.

---

## Component 6: Full REPL Command Palette

### Problem

REPL works but uses raw `input()` (no history, no arrow keys) and only supports `/exit`.

### Solution

**6a. Readline integration** (`chimera/cli/code.py`)

Import `readline`. Set up persistent history at `~/.chimera/history`. Auto-load on start, auto-save on exit. Tab completion for slash commands via `readline.set_completer()`.

**6b. Slash command dispatcher**

Registry dict mapping command names to handler functions. Each handler: `(session, env, args_str) -> None`. Input starting with `/` is dispatched; everything else goes to the agent.

```python
_COMMANDS: dict[str, CommandHandler] = {
    "help": cmd_help,        # list all commands
    "model": cmd_model,      # show current model or switch
    "cost": cmd_cost,        # show cumulative cost + per-model breakdown
    "clear": cmd_clear,      # reset conversation context
    "history": cmd_history,  # show recent conversation turns
    "tools": cmd_tools,      # list available tools with descriptions
    "context": cmd_context,  # show context message count and estimated tokens
    "debug": cmd_debug,      # toggle debug mode (raw messages, token counts)
    "session": cmd_session,  # save / load / list / fork
    "compact": cmd_compact,  # trigger manual context compaction
    "exit": cmd_exit,        # exit the REPL
    "quit": cmd_exit,        # alias
}
```

**6c. Session persistence**

- `/session save [name]` — calls `session.save()` with `FileStorage("~/.chimera/sessions/")`
- `/session load <id>` — calls `Session.resume()`
- `/session list` — lists saved session files
- `/session fork` — calls `session.fork()`, switches to forked session

**6d. Debug mode**

`/debug` toggles a flag. When on, wraps ConsoleStreamHandler in a `DebugStreamHandler` that additionally prints:
- Raw tool call arguments
- Token counts per step
- Full assistant message content (not just streamed text)

---

## What This Completes

After implementation, Chimera has:

```
Feature                         Before          After
───────────────────────────────────────────────────────
Async loop                      async_run()     async_iter_steps() + concurrent tools
Parallel execution              Threads only    Threads + asyncio.gather()
Early cancellation              No              first_success mode
MCP reliability                 Happy path      Retry + health checks + stderr
LSP diagnostics                 Broken          Background reader + cached
LSP methods                     4               7 (+ workspace/symbol, code_actions, completion)
Cost tracking                   Per-response    Cumulative + budgets + estimation
REPL                            input() + /exit readline + 12 commands + session mgmt
```

The framework becomes production-grade: async throughout, resilient external integrations, developer-friendly REPL with full introspection.
