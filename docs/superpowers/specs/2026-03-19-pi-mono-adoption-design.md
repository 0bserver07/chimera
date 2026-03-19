# Pi-Mono Pattern Adoption for Chimera

**Date:** 2026-03-19
**Status:** Draft (rev 2 — post spec review)
**Source:** pi-mono monorepo (`@mariozechner/pi-*` packages)

## Overview

Seven architectural patterns from pi-mono that Chimera should adopt as new layers/components. Each feature is designed as a standalone block that slots into Chimera's existing 8-layer stack without breaking existing APIs.

---

## Feature 1: Message Queues (Steering + Follow-up)

### Problem

Chimera's agent loop is fire-and-forget. Once `Agent.run()` or `Session.chat()` starts, there is no way to inject messages mid-turn (steer the agent) or queue messages for after the turn completes. Interactive use cases (`chimera code` REPL) need both.

### What Pi Does

Pi's `Agent` class has two message queues:

- **Steering queue**: Messages injected *during* a turn. The inner loop checks `getSteeringMessages()` after each tool execution cycle and injects them before the next LLM call. This lets users redirect the agent mid-task ("actually, use pytest instead of unittest").
- **Follow-up queue**: Messages queued while the agent is running, processed *after* the current turn ends. The outer loop checks `getFollowUpMessages()` and starts a new turn for each. This lets users type ahead without interrupting.

### Design

#### New module: `chimera/core/message_queue.py`

```python
from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from chimera.types import Message


@dataclass
class MessageQueues:
    """Thread-safe steering and follow-up message queues.

    Steering messages are injected into the current turn's context
    before the next LLM call. Follow-up messages trigger new turns
    after the current one completes.
    """

    _steering: deque[Message] = field(default_factory=deque)
    _follow_up: deque[Message] = field(default_factory=deque)
    _lock: Lock = field(default_factory=Lock)

    def steer(self, message: Message) -> None:
        """Add a steering message (injected mid-turn)."""
        with self._lock:
            self._steering.append(message)

    def follow_up(self, message: Message) -> None:
        """Add a follow-up message (processed after current turn)."""
        with self._lock:
            self._follow_up.append(message)

    def drain_steering(self) -> list[Message]:
        """Pop all pending steering messages."""
        with self._lock:
            msgs = list(self._steering)
            self._steering.clear()
            return msgs

    def drain_follow_up(self) -> list[Message]:
        """Pop all pending follow-up messages."""
        with self._lock:
            msgs = list(self._follow_up)
            self._follow_up.clear()
            return msgs

    @property
    def has_steering(self) -> bool:
        with self._lock:
            return len(self._steering) > 0

    @property
    def has_follow_up(self) -> bool:
        with self._lock:
            return len(self._follow_up) > 0
```

#### Changes to `LoopConfig`

Add `message_queues: MessageQueues | None = None` field.

#### Changes to `ReAct.iter_steps()`

After each tool execution cycle (before looping back for the next LLM call), check for steering messages:

```python
# After tool execution, before next LLM call:
if self.config and self.config.message_queues:
    for msg in self.config.message_queues.drain_steering():
        context.add(msg)
```

#### Changes to `ReAct.run()` / `Session.chat()`

Wrap the existing loop in a follow-up outer loop:

```python
while True:
    result = drain_steps(self.iter_steps(provider, tools, context, env))
    if not (self.config and self.config.message_queues
            and self.config.message_queues.has_follow_up):
        break
    for msg in self.config.message_queues.drain_follow_up():
        context.add(msg)
```

#### Changes to `Session`

Expose steering/follow-up on the Session for REPL use:

```python
def steer(self, message: str) -> None:
    """Inject a steering message into the running turn."""
    queues = self._agent.loop.config.message_queues
    if queues:
        queues.steer(Message.user(message))

def queue(self, message: str) -> None:
    """Queue a follow-up message for after the current turn."""
    queues = self._agent.loop.config.message_queues
    if queues:
        queues.follow_up(Message.user(message))
```

#### `iter_steps()` / `iter_chat()` Limitation

Follow-up processing only works with `run()` / `chat()`. When using the generator-based `iter_steps()` or `iter_chat()`, the consumer drives iteration and the generator returns `AgentResult` via `StopIteration.value` after a single turn. The consumer is responsible for checking `has_follow_up` after the generator completes and starting a new iteration:

```python
# Consumer pattern for iter_chat with follow-up:
while True:
    gen = session.iter_chat(message)
    result = drain_steps(gen)
    queues = session._agent.loop.config.message_queues
    if not (queues and queues.has_follow_up):
        break
    for msg in queues.drain_follow_up():
        message = msg.content  # Next turn
```

Steering works in both modes — the loop checks between steps regardless of whether it's driven by `run()` or `iter_steps()`.

### Files Changed

| File | Change |
|------|--------|
| `chimera/core/message_queue.py` | **New** — MessageQueues dataclass |
| `chimera/core/loop_config.py` | Add `message_queues` field |
| `chimera/core/loop.py` | Steering drain after tool exec; follow-up outer loop |
| `chimera/sessions/session.py` | Add `steer()` and `queue()` methods |
| `tests/test_message_queue.py` | **New** — unit tests |

### Backward Compatibility

When `message_queues` is `None` (default), behavior is unchanged.

---

## Feature 2: Provider Registry

### Problem

`chimera/providers/factory.py` uses a static if/elif chain to dispatch provider creation. Adding a new provider requires modifying factory code. Plugins cannot register custom providers at runtime.

### What Pi Does

Pi uses a registry pattern: `registerApiProvider("anthropic-messages", anthropicStream)` / `getApiProvider("anthropic-messages")`. Providers register themselves at import time. Extensions can register new providers at runtime.

### Design

#### New module: `chimera/providers/registry.py`

```python
from __future__ import annotations
from typing import Any, Callable
from chimera.providers.base import Provider

# Type for provider constructor functions
ProviderFactory = Callable[..., Provider]

_registry: dict[str, ProviderFactory] = {}


def register_provider(name: str, factory: ProviderFactory) -> None:
    """Register a provider factory by name.

    Args:
        name: Provider type name (e.g. "anthropic", "openai").
        factory: Callable that accepts (model, api_key, base_url, **kwargs)
            and returns a Provider instance.
    """
    _registry[name] = factory


def get_provider_factory(name: str) -> ProviderFactory | None:
    """Look up a registered provider factory by name."""
    return _registry.get(name)


def list_providers() -> list[str]:
    """Return all registered provider names."""
    return list(_registry.keys())


def unregister_provider(name: str) -> None:
    """Remove a provider from the registry."""
    _registry.pop(name, None)
```

#### Self-registration in each provider module

Each existing provider registers itself at module level:

```python
# chimera/providers/anthropic.py (at bottom)
from chimera.providers.registry import register_provider

def _create_anthropic(model: str, api_key: str | None = None,
                      base_url: str | None = None, **kwargs) -> AnthropicProvider:
    return AnthropicProvider(model=model, api_key=api_key, base_url=base_url)

register_provider("anthropic", _create_anthropic)
```

#### Bootstrap: `_ensure_builtins_registered()`

Providers self-register at module level, but are currently imported lazily inside `create_provider()`. To ensure registration happens before the first `get_provider_factory()` call, add a bootstrap function:

```python
# chimera/providers/registry.py

_builtins_registered = False

def _ensure_builtins_registered() -> None:
    """Import all built-in provider modules to trigger self-registration.

    Called once from create_provider(). After the first call, this is a no-op.
    """
    global _builtins_registered
    if _builtins_registered:
        return
    _builtins_registered = True
    import chimera.providers.anthropic      # noqa: F401
    import chimera.providers.openai_provider  # noqa: F401
    import chimera.providers.google         # noqa: F401
    import chimera.providers.ollama         # noqa: F401
    import chimera.providers.compatible     # noqa: F401
    import chimera.providers.modal          # noqa: F401
```

The self-registration imports at the bottom of each provider module are safe because `registry.py` has zero provider dependencies — it only defines the dict and helper functions.

#### Refactored `create_provider()`

```python
def create_provider(provider_type: str | None = None, *, model: str | None = None,
                    api_key: str | None = None, base_url: str | None = None,
                    **kwargs) -> Provider:
    _ensure_builtins_registered()

    if model is None:
        # ... existing env var fallback ...

    if provider_type is None:
        provider_type = _infer_provider(model)

    # Try registry first
    factory = get_provider_factory(provider_type)
    if factory is not None:
        return factory(model=model, api_key=api_key, base_url=base_url, **kwargs)

    raise ValueError(f"Unknown provider: '{provider_type}'. "
                     f"Registered: {list_providers()}")
```

#### Plugin integration

The existing `PluginExtensionRegistry` gets a new hook:

```python
# In chimera/plugins/registry.py
def register_provider(self, name: str, factory: ProviderFactory) -> None:
    from chimera.providers.registry import register_provider
    register_provider(name, factory)
```

### Files Changed

| File | Change |
|------|--------|
| `chimera/providers/registry.py` | **New** — registry module |
| `chimera/providers/factory.py` | Refactor to use registry lookup |
| `chimera/providers/anthropic.py` | Self-register |
| `chimera/providers/openai_provider.py` | Self-register |
| `chimera/providers/google.py` | Self-register |
| `chimera/providers/ollama.py` | Self-register |
| `chimera/providers/compatible.py` | Self-register |
| `chimera/providers/modal.py` | Self-register |
| `chimera/plugins/registry.py` | Add `register_provider()` hook |
| `tests/test_provider_registry.py` | **New** |

### Backward Compatibility

`create_provider()` signature unchanged. Existing code works identically. The if/elif chain is replaced by registry lookup — same behavior, more extensible.

---

## Feature 3: Tool Operations (Pluggable Backends)

### Problem

Chimera tools are tightly coupled to `Environment`. `ReadFileTool.execute()` calls `env.read_file()`, `BashTool.execute()` calls `env.run_command()`. You can't mix backends (e.g., read files locally but run bash in Docker) without a custom Environment that wraps both. The Environment ABC is also monolithic — it forces every implementation to provide `read_file`, `write_file`, `run_command`, `run_tests`, `checkpoint`, `restore`, even if some don't apply.

### What Pi Does

Pi decouples tool logic from tool backend via per-tool operation interfaces:

```typescript
interface ReadOperations {
    readFile(path: string): Promise<Buffer>;
    access(path: string): Promise<void>;
}
createReadTool(cwd, operations?)  // default = local filesystem
```

Each tool has its own operations interface. You can swap the read backend independently from the bash backend.

### Design

#### New module: `chimera/core/operations.py`

```python
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable
from chimera.types import CommandResult


@runtime_checkable
class ReadOps(Protocol):
    """Backend for file reading."""
    def read_file(self, path: str) -> str: ...
    def file_exists(self, path: str) -> bool: ...


@runtime_checkable
class WriteOps(Protocol):
    """Backend for file writing."""
    def write_file(self, path: str, content: str) -> None: ...


@runtime_checkable
class BashOps(Protocol):
    """Backend for command execution."""
    def run_command(self, command: str, timeout: int = 120,
                    cwd: str | None = None) -> CommandResult: ...


@runtime_checkable
class SearchOps(Protocol):
    """Backend for file search (grep/find)."""
    def search_files(self, pattern: str, path: str = ".") -> list[str]: ...
    def list_files(self, pattern: str = "**/*") -> list[str]: ...


class LocalReadOps:
    """Default ReadOps using local filesystem."""

    def __init__(self, cwd: str = ".") -> None:
        self.cwd = cwd

    def read_file(self, path: str) -> str:
        import os
        full = os.path.join(self.cwd, path) if not os.path.isabs(path) else path
        with open(full) as f:
            return f.read()

    def file_exists(self, path: str) -> bool:
        import os
        full = os.path.join(self.cwd, path) if not os.path.isabs(path) else path
        return os.path.exists(full)


class LocalBashOps:
    """Default BashOps using subprocess."""

    def __init__(self, cwd: str = ".") -> None:
        self.cwd = cwd

    def run_command(self, command: str, timeout: int = 120,
                    cwd: str | None = None) -> CommandResult:
        import subprocess
        work_dir = cwd or self.cwd
        try:
            result = subprocess.run(
                command, shell=True, cwd=work_dir,
                capture_output=True, text=True, timeout=timeout,
            )
            return CommandResult(
                stdout=result.stdout, stderr=result.stderr,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(stdout="", stderr="Timeout", exit_code=-1)


# ... LocalWriteOps, LocalSearchOps follow same pattern
```

#### Refactored tools accept operations

```python
class ReadFileTool(BaseTool):
    name = "read_file"
    # ...

    def __init__(self, ops: ReadOps | None = None) -> None:
        self._ops = ops

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        ops = self._ops
        if ops is None and env is not None:
            # Backward compat: use env as ops
            return self._execute_via_env(args, env)
        if ops is None:
            ops = LocalReadOps()
        try:
            content = ops.read_file(args["path"])
            return ToolResult(output=content)
        except FileNotFoundError:
            return ToolResult(output="", error=f"File not found: {args['path']}")

    def _execute_via_env(self, args, env):
        # Existing behavior preserved
        try:
            content = env.read_file(args["path"])
            return ToolResult(output=content)
        except FileNotFoundError:
            return ToolResult(output="", error=f"File not found: {args['path']}")
```

#### Environment as operations adapter

The existing `Environment` can be wrapped to provide operations:

```python
class EnvironmentReadOps:
    """Adapts an Environment to ReadOps protocol."""
    def __init__(self, env: Environment) -> None:
        self._env = env

    def read_file(self, path: str) -> str:
        return self._env.read_file(path)

    def file_exists(self, path: str) -> bool:
        try:
            self._env.read_file(path)
            return True
        except FileNotFoundError:
            return False
```

#### How operations are injected into tools

Tools are currently passed as a flat list to `Agent.__init__()` and constructed via `DEFAULT_TOOLS` in `chimera/core/tool_group.py`. Operations injection works at tool construction time:

```python
# chimera/core/tool_group.py — updated DEFAULT_TOOLS factory

def create_default_tools(
    read_ops: ReadOps | None = None,
    write_ops: WriteOps | None = None,
    bash_ops: BashOps | None = None,
    search_ops: SearchOps | None = None,
) -> list[BaseTool]:
    """Create default tool set with optional operation backends."""
    return [
        ReadFileTool(ops=read_ops),
        WriteFileTool(ops=write_ops),
        EditFileTool(read_ops=read_ops, write_ops=write_ops),
        BashTool(ops=bash_ops),
        SearchTool(ops=search_ops),
        ListFilesTool(ops=search_ops),
        # ... remaining tools that don't use operations ...
    ]
```

For the `Agent` convenience API:

```python
agent = Agent(
    provider=provider,
    tools=create_default_tools(bash_ops=DockerBashOps(container_id="abc")),
)
```

The existing `AGENT_TOOLS` constant remains available as `create_default_tools()` with no arguments (local filesystem defaults).

### Files Changed

| File | Change |
|------|--------|
| `chimera/core/operations.py` | **New** — Protocol interfaces + local defaults |
| `chimera/core/tool_group.py` | Update `create_default_tools()` to accept ops |
| `chimera/tools/read.py` | Accept optional `ReadOps` |
| `chimera/tools/write.py` | Accept optional `WriteOps` |
| `chimera/tools/bash.py` | Accept optional `BashOps` |
| `chimera/tools/search.py` | Accept optional `SearchOps` |
| `chimera/tools/edit.py` | Accept optional `WriteOps` + `ReadOps` |
| `chimera/tools/list_files.py` | Accept optional `SearchOps` |
| `tests/test_operations.py` | **New** |

### Backward Compatibility

All tools continue to accept `env: Environment | None` as before. When `ops` is `None` and `env` is provided, the tool delegates to `env` (existing behavior). When `ops` is provided, it takes precedence. When both are `None`, local filesystem defaults are used.

The `Environment` ABC is NOT changed — existing environments continue to work. This is additive.

---

## Feature 4: Session Tree (JSONL with Branching)

### Problem

Chimera sessions are linear — a flat list of messages. `Session.fork()` creates a deep copy with a new ID but no structural connection. There's no way to navigate branches, backtrack to a previous point, or visualize the conversation tree. The event log (`EventLog`) stores events as individual files, not session messages.

### What Pi Does

Pi persists sessions as a JSONL file where each entry has `id` and `parentId`. This creates a tree:
- Fork = append new entries with `parentId` pointing to the branch point (same file, no duplication)
- Navigation = walk the `parentId` chain from any leaf to the root
- `/tree` command shows the full tree and lets you switch branches

### Design

#### New module: `chimera/sessions/tree.py`

```python
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.types import Message


@dataclass
class SessionEntry:
    """A single entry in the session log."""

    type: str  # "header", "message", "compaction", "model_change", "label", "fork"
    id: str
    parent_id: str | None
    timestamp: float
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionHeader(SessionEntry):
    """First entry in a session file."""

    type: str = "header"
    version: int = 1
    cwd: str = ""
    system_prompt: str = ""


@dataclass
class MessageEntry(SessionEntry):
    """A conversation message (user, assistant, tool)."""

    type: str = "message"
    message: Message | None = None


@dataclass
class CompactionEntry(SessionEntry):
    """Records a compaction event with file tracking."""

    type: str = "compaction"
    summary: str = ""
    first_kept_entry_id: str = ""
    tokens_before: int = 0
    read_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)


@dataclass
class LabelEntry(SessionEntry):
    """Bookmark a specific entry (e.g., 'checkpoint-1')."""

    type: str = "label"
    target_id: str = ""
    label: str = ""


class SessionTree:
    """JSONL-based session persistence with in-place branching.

    Each session is a single JSONL file. Entries form a tree via parent_id.
    Branching appends new entries pointing to the branch point — no file
    duplication.

    Args:
        path: Path to the JSONL session file.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._entries: list[SessionEntry] = []
        self._by_id: dict[str, SessionEntry] = {}
        self._children: dict[str | None, list[str]] = {}  # parent_id -> [child_ids]
        self._active_leaf: str | None = None
        if self._path.exists():
            self._load()

    def append(self, entry: SessionEntry) -> None:
        """Append an entry to the log and persist it."""
        self._entries.append(entry)
        self._by_id[entry.id] = entry
        self._children.setdefault(entry.parent_id, []).append(entry.id)
        self._active_leaf = entry.id
        self._append_to_file(entry)

    def add_message(self, message: Message, parent_id: str | None = None) -> str:
        """Add a message entry, returning its ID."""
        entry_id = self._generate_id()
        pid = parent_id or self._active_leaf
        entry = MessageEntry(
            id=entry_id,
            parent_id=pid,
            timestamp=time.time(),
            message=message,
        )
        self.append(entry)
        return entry_id

    def add_compaction(self, summary: str, first_kept_id: str,
                       tokens_before: int,
                       read_files: list[str] | None = None,
                       modified_files: list[str] | None = None) -> str:
        """Record a compaction event with file tracking metadata."""
        entry_id = self._generate_id()
        entry = CompactionEntry(
            id=entry_id,
            parent_id=self._active_leaf,
            timestamp=time.time(),
            summary=summary,
            first_kept_entry_id=first_kept_id,
            tokens_before=tokens_before,
            read_files=read_files or [],
            modified_files=modified_files or [],
        )
        self.append(entry)
        return entry_id

    def get_branch(self, leaf_id: str | None = None) -> list[SessionEntry]:
        """Walk from leaf to root, return entries in chronological order."""
        leaf = leaf_id or self._active_leaf
        if leaf is None:
            return []
        chain: list[SessionEntry] = []
        current = leaf
        while current is not None:
            entry = self._by_id.get(current)
            if entry is None:
                break
            chain.append(entry)
            current = entry.parent_id
        chain.reverse()
        return chain

    def get_messages(self, leaf_id: str | None = None) -> list[Message]:
        """Get conversation messages for a branch, in order."""
        branch = self.get_branch(leaf_id)
        messages: list[Message] = []
        for entry in branch:
            if isinstance(entry, MessageEntry) and entry.message is not None:
                messages.append(entry.message)
            elif isinstance(entry, CompactionEntry):
                # Insert summary as a system-like message
                messages.append(Message.user(
                    f"[Session compacted. Summary: {entry.summary}]"
                ))
        return messages

    def fork(self, from_entry_id: str) -> str:
        """Create a branch point. Returns the entry ID to use as parent for new messages."""
        if from_entry_id not in self._by_id:
            raise ValueError(f"Entry {from_entry_id} not found")
        self._active_leaf = from_entry_id
        return from_entry_id

    def get_branch_points(self) -> list[str]:
        """Return entry IDs that have more than one child (branch points)."""
        return [
            pid for pid, children in self._children.items()
            if pid is not None and len(children) > 1
        ]

    def get_leaves(self) -> list[str]:
        """Return entry IDs that have no children (leaf nodes)."""
        all_parents = set()
        for entry in self._entries:
            if entry.parent_id is not None:
                all_parents.add(entry.parent_id)
        return [e.id for e in self._entries if e.id not in all_parents]

    def switch_branch(self, leaf_id: str) -> None:
        """Switch the active branch to the given leaf."""
        if leaf_id not in self._by_id:
            raise ValueError(f"Entry {leaf_id} not found")
        self._active_leaf = leaf_id

    @property
    def active_leaf(self) -> str | None:
        return self._active_leaf

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    # --- File I/O ---

    def _load(self) -> None:
        """Load entries from JSONL file."""
        with open(self._path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    entry = self._deserialize(raw)
                    self._entries.append(entry)
                    self._by_id[entry.id] = entry
                    self._children.setdefault(entry.parent_id, []).append(entry.id)
                    self._active_leaf = entry.id
                except (json.JSONDecodeError, KeyError):
                    continue  # Skip corrupt entries (crash recovery)

    def _append_to_file(self, entry: SessionEntry) -> None:
        """Append a single entry to the JSONL file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a") as f:
            f.write(json.dumps(self._serialize(entry)) + "\n")

    def _serialize(self, entry: SessionEntry) -> dict[str, Any]:
        """Serialize entry to dict."""
        d: dict[str, Any] = {
            "type": entry.type,
            "id": entry.id,
            "parent_id": entry.parent_id,
            "timestamp": entry.timestamp,
        }
        if isinstance(entry, MessageEntry) and entry.message:
            d["message"] = {
                "role": entry.message.role,
                "content": entry.message.content,
            }
            # Preserve tool_calls on assistant messages
            if hasattr(entry.message, "tool_calls") and entry.message.tool_calls:
                d["message"]["tool_calls"] = [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in entry.message.tool_calls
                ]
            # Preserve call_id on tool-result messages
            if hasattr(entry.message, "call_id") and entry.message.call_id:
                d["message"]["call_id"] = entry.message.call_id
        elif isinstance(entry, CompactionEntry):
            d["summary"] = entry.summary
            d["first_kept_entry_id"] = entry.first_kept_entry_id
            d["tokens_before"] = entry.tokens_before
            d["read_files"] = entry.read_files
            d["modified_files"] = entry.modified_files
        elif isinstance(entry, SessionHeader):
            d["version"] = entry.version
            d["cwd"] = entry.cwd
            d["system_prompt"] = entry.system_prompt
        elif isinstance(entry, LabelEntry):
            d["target_id"] = entry.target_id
            d["label"] = entry.label
        return d

    def _deserialize(self, raw: dict[str, Any]) -> SessionEntry:
        """Deserialize dict to entry."""
        entry_type = raw["type"]
        base = {
            "id": raw["id"],
            "parent_id": raw.get("parent_id"),
            "timestamp": raw.get("timestamp", 0),
        }

        if entry_type == "header":
            return SessionHeader(
                **base,
                version=raw.get("version", 1),
                cwd=raw.get("cwd", ""),
                system_prompt=raw.get("system_prompt", ""),
            )
        elif entry_type == "message":
            msg_data = raw.get("message", {})
            msg = Message(role=msg_data["role"], content=msg_data.get("content", ""))
            # Restore tool_calls on assistant messages
            if "tool_calls" in msg_data:
                msg.tool_calls = [
                    ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"])
                    for tc in msg_data["tool_calls"]
                ]
            # Restore call_id on tool-result messages
            if "call_id" in msg_data:
                msg.call_id = msg_data["call_id"]
            return MessageEntry(**base, message=msg)
        elif entry_type == "compaction":
            return CompactionEntry(
                **base,
                summary=raw.get("summary", ""),
                first_kept_entry_id=raw.get("first_kept_entry_id", ""),
                tokens_before=raw.get("tokens_before", 0),
                read_files=raw.get("read_files", []),
                modified_files=raw.get("modified_files", []),
            )
        elif entry_type == "label":
            return LabelEntry(
                **base,
                target_id=raw.get("target_id", ""),
                label=raw.get("label", ""),
            )
        else:
            return SessionEntry(type=entry_type, **base, data=raw)

    @staticmethod
    def _generate_id() -> str:
        return uuid.uuid4().hex[:12]
```

#### Integration with `Session`

```python
class Session:
    def __init__(self, ..., tree: SessionTree | None = None):
        self._tree = tree
        # ... existing init ...

    def chat(self, message: str) -> AgentResult:
        self._context.add(Message.user(message))
        if self._tree:
            self._tree.add_message(Message.user(message))
        result = self._agent.loop.run(...)
        if self._tree:
            self._tree.add_message(Message.assistant(result.output))
        return result

    def fork(self, from_entry_id: str | None = None) -> Session:
        """Fork at entry_id (or current position). Returns new Session on the fork."""
        if self._tree and from_entry_id:
            self._tree.fork(from_entry_id)
        # ... build new session from tree branch ...

    def switch_branch(self, leaf_id: str) -> None:
        """Switch to a different branch and rebuild context."""
        if self._tree:
            self._tree.switch_branch(leaf_id)
            messages = self._tree.get_messages(leaf_id)
            self._context = Context(system=self._context.system)
            for msg in messages:
                self._context.add(msg)
```

### CLI Commands

New slash commands for `chimera code`:

- `/tree` — show conversation tree with branch points
- `/branch <entry_id>` — fork from a specific entry
- `/switch <leaf_id>` — switch to a different branch

### Files Changed

| File | Change |
|------|--------|
| `chimera/sessions/tree.py` | **New** — SessionTree, entry types |
| `chimera/sessions/session.py` | Add `tree` param, `switch_branch()` |
| `chimera/cli/code.py` | Add `/tree`, `/branch`, `/switch` commands |
| `tests/test_session_tree.py` | **New** |

### Relationship to `EventSourcedSession`

Chimera already has `EventSourcedSession` in `chimera/sessions/eventlog/session.py`, which journals interactions to an append-only `EventLog` (one JSON file per event). `SessionTree` is a **replacement** for `EventSourcedSession`, not a complement:

| Aspect | EventSourcedSession | SessionTree |
|--------|-------------------|-------------|
| Storage | One file per event in a directory | Single JSONL file |
| Branching | None | Tree via `parent_id` |
| Navigation | Sequential only | Branch/leaf traversal |
| Compaction metadata | None | `read_files`, `modified_files` |
| Crash recovery | Skip corrupt files | Skip corrupt lines |

`EventSourcedSession` remains available for event-level persistence (security auditing, replay). `SessionTree` is for conversation persistence with branching. They serve different purposes but `SessionTree` replaces `EventSourcedSession` as the default session storage for interactive use.

### Backward Compatibility

`SessionTree` is opt-in. Existing `Session(storage=...)` continues to work. When `tree` is provided, messages are additionally persisted to the JSONL tree. The tree can be used standalone or alongside existing storage.

---

## Feature 5: Compaction File Tracking

### Problem

When Chimera compacts context (via `SummaryCompaction` or `ThresholdCompaction`), it loses track of which files the agent has read and modified. After compaction, the agent doesn't know what files it was working on.

### What Pi Does

Pi's `CompactionEntry` includes `readFiles` and `modifiedFiles` arrays. These are accumulated across compaction rounds. After compaction, the system prompt includes a "files you've been working with" section so the agent retains awareness.

### Design

#### New dataclass: `CompactionMetadata`

```python
@dataclass
class CompactionMetadata:
    """Metadata preserved across compaction boundaries."""

    read_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0

    def merge(self, other: CompactionMetadata) -> CompactionMetadata:
        """Merge another metadata into this one (union of file lists)."""
        return CompactionMetadata(
            read_files=list(dict.fromkeys(self.read_files + other.read_files)),
            modified_files=list(dict.fromkeys(self.modified_files + other.modified_files)),
            tokens_before=other.tokens_before,
            tokens_after=other.tokens_after,
        )
```

#### File tracking in tool executor

The tool executor already knows which tools are file-modifying (`_FILE_MODIFYING_TOOLS`). Extend this to also track reads:

```python
_FILE_READING_TOOLS = frozenset({"read_file"})

# In execute_tool_calls_incremental, after successful execution:
if config and config.file_tracker:
    if tc.name in _FILE_READING_TOOLS:
        path = tc.arguments.get("path", "")
        if path:
            config.file_tracker.record_read(path)
    elif tc.name in _FILE_MODIFYING_TOOLS:
        path = tc.arguments.get("path", "")
        if path:
            config.file_tracker.record_modified(path)
```

#### New module: `chimera/core/file_tracker.py`

```python
@dataclass
class FileTracker:
    """Tracks files read and modified during agent execution."""

    read_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    _seen_read: set[str] = field(default_factory=set)
    _seen_modified: set[str] = field(default_factory=set)

    def record_read(self, path: str) -> None:
        if path not in self._seen_read:
            self._seen_read.add(path)
            self.read_files.append(path)

    def record_modified(self, path: str) -> None:
        if path not in self._seen_modified:
            self._seen_modified.add(path)
            self.modified_files.append(path)

    def to_metadata(self) -> CompactionMetadata:
        return CompactionMetadata(
            read_files=list(self.read_files),
            modified_files=list(self.modified_files),
        )

    def to_prompt_section(self) -> str:
        """Generate a prompt section listing tracked files."""
        if not self.read_files and not self.modified_files:
            return ""
        lines = ["## Files you've been working with"]
        if self.modified_files:
            lines.append("Modified: " + ", ".join(self.modified_files))
        if self.read_files:
            lines.append("Read: " + ", ".join(self.read_files))
        return "\n".join(lines)
```

#### Integration with compaction strategies

The `CompactionStrategy.compact()` ABC signature is **NOT changed**. Instead, `FileTracker` metadata is passed to compaction via a separate method on strategies that support it:

```python
class FileAwareCompaction(CompactionStrategy):
    """Mixin for compaction strategies that use file tracking metadata."""

    def set_metadata(self, metadata: CompactionMetadata) -> None:
        """Inject file tracking metadata before compaction."""
        self._file_metadata = metadata

    def get_file_prompt_section(self) -> str:
        """Generate prompt section listing tracked files."""
        if not hasattr(self, "_file_metadata"):
            return ""
        return self._file_metadata.to_prompt_section()
```

`SummaryCompaction` extends `FileAwareCompaction` and includes the file list in its summarization prompt. Other strategies (`PruneCompaction`, `CounterCompaction`, etc.) are unaffected — they don't need to know about file tracking.

The compaction caller (in `ReAct` or `Session`) checks `isinstance(strategy, FileAwareCompaction)` before calling `set_metadata()`:

```python
if file_tracker and isinstance(compaction, FileAwareCompaction):
    compaction.set_metadata(file_tracker.to_metadata())
compacted = compaction.compact(messages, budget)
```

This preserves full backward compatibility for all existing `CompactionStrategy` subclasses and any third-party strategies.

### Files Changed

| File | Change |
|------|--------|
| `chimera/core/file_tracker.py` | **New** — FileTracker |
| `chimera/compaction/base.py` | Add `CompactionMetadata` dataclass |
| `chimera/compaction/strategies.py` | Pass metadata to summary prompt |
| `chimera/core/loop_config.py` | Add `file_tracker: FileTracker | None` field |
| `chimera/core/tool_executor.py` | Record reads/writes to file tracker |
| `tests/test_file_tracker.py` | **New** |

### Backward Compatibility

`CompactionMetadata` parameter defaults to `None`. Existing compaction strategies work unchanged. `FileTracker` is opt-in via `LoopConfig`.

---

## Feature 6: Cancellation Token

### Problem

Chimera has no systematic way to cancel a running operation. If a tool hangs (e.g., a bash command that blocks), or the user wants to abort mid-turn, there's no clean cancellation path. The agent loop, tool executor, and individual tools all lack cancellation support.

### What Pi Does

Pi threads `AbortSignal` through every layer:
- Agent loop checks signal before each LLM call
- Tool execution passes signal to each tool
- Bash tool kills the process tree on abort
- LLM streaming checks signal between chunks

### Design

#### New module: `chimera/core/cancellation.py`

```python
from __future__ import annotations

import threading
from typing import Callable


class CancellationToken:
    """Cooperative cancellation token.

    Thread-safe. Any layer can check `is_cancelled` and any layer can
    call `cancel()` to signal all holders.
    """

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    def cancel(self) -> None:
        """Signal cancellation."""
        self._cancelled.set()
        with self._lock:
            for cb in self._callbacks:
                cb()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check(self) -> None:
        """Raise OperationCancelled if cancelled."""
        if self._cancelled.is_set():
            raise OperationCancelled("Operation cancelled")

    def on_cancel(self, callback: Callable[[], None]) -> None:
        """Register a callback to run when cancelled."""
        with self._lock:
            if self._cancelled.is_set():
                callback()
            else:
                self._callbacks.append(callback)

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for cancellation. Returns True if cancelled."""
        return self._cancelled.wait(timeout)


class OperationCancelled(Exception):
    """Raised when a cancellation token is checked after cancel().

    Named OperationCancelled (not CancelledError) to avoid conflict
    with asyncio.CancelledError.
    """
    pass
```

#### Integration points

**LoopConfig**: Add `cancellation: CancellationToken | None = None`

**ReAct loop**: Check before each LLM call and after each tool execution:
```python
if self.config and self.config.cancellation:
    self.config.cancellation.check()
```

**Tool executor**: Check before and after each tool call (NOT changing tool signatures):
```python
# In execute_tool_calls_incremental, before executing each tool:
if config and config.cancellation:
    config.cancellation.check()

# After executing each tool:
if config and config.cancellation and config.cancellation.is_cancelled:
    raise OperationCancelled("Cancelled during tool execution")
```

**BaseTool.execute() is NOT changed.** The `execute(self, args, env)` signature stays the same for all 22 existing tools. Cancellation is handled at the executor level.

**BashTool**: For tools that need cooperative cancellation (long-running processes), the tool accesses the token via an injectable attribute rather than a parameter:

```python
class CancellableTool(BaseTool):
    """Mixin for tools that support cooperative cancellation."""

    _cancel_token: CancellationToken | None = None

    def bind_cancellation(self, token: CancellationToken) -> None:
        self._cancel_token = token


class BashTool(CancellableTool):
    def execute(self, args, env):
        # ... existing signature unchanged ...
        process = subprocess.Popen(...)
        if self._cancel_token:
            self._cancel_token.on_cancel(lambda: process.kill())
        # ...
```

The tool executor calls `bind_cancellation()` on tools that are `isinstance(tool, CancellableTool)` before executing them. This is opt-in per tool.

**Session**: Expose cancel:
```python
class Session:
    def cancel(self) -> None:
        """Cancel the running agent turn."""
        if self._cancel_token:
            self._cancel_token.cancel()
```

### Files Changed

| File | Change |
|------|--------|
| `chimera/core/cancellation.py` | **New** — CancellationToken, OperationCancelled, CancellableTool mixin |
| `chimera/core/loop_config.py` | Add `cancellation` field |
| `chimera/core/loop.py` | Check token before LLM calls |
| `chimera/core/tool_executor.py` | Check token before/after tool calls, bind to CancellableTools |
| `chimera/tools/bash.py` | Extend `CancellableTool`, kill on cancel |
| `chimera/sessions/session.py` | Add `cancel()` method |
| `tests/test_cancellation.py` | **New** |

### Backward Compatibility

**BaseTool.execute() signature is unchanged.** No existing tool implementations break. Only tools that opt in to `CancellableTool` gain cooperative cancellation. The executor handles cancellation at the boundary — all tools get checked before/after execution regardless of whether they support cooperative cancellation.

---

## Feature 7: RPC Mode

### Problem

Chimera can only run as a CLI or be used programmatically via Python imports. There's no way to embed Chimera in non-Python environments (IDE extensions, web apps, Electron apps) without the full Python runtime. The existing `Wire` protocol provides message types but no transport layer.

### What Pi Does

Pi has an RPC mode (`--mode rpc`) that reads JSON commands from stdin and emits JSON events/responses to stdout. This enables:
- IDE extensions (VSCode, JetBrains) to control pi via subprocess
- Web apps to communicate via a thin bridge process
- Non-Node environments to use pi without importing the Node SDK

### Design

#### New module: `chimera/rpc/`

```
chimera/rpc/
  __init__.py
  types.py      — RPC command/response/event types
  server.py     — stdin/stdout JSON-RPC server
  handler.py    — Command handlers (prompt, steer, compact, get_state, etc.)
```

#### `chimera/rpc/types.py`

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


# --- Commands (client -> server) ---

@dataclass
class RpcCommand:
    """Base for all RPC commands."""
    type: str
    id: str = ""  # For request/response correlation


@dataclass
class PromptCommand(RpcCommand):
    type: str = "prompt"
    message: str = ""
    images: list[dict[str, str]] = field(default_factory=list)


@dataclass
class SteerCommand(RpcCommand):
    type: str = "steer"
    message: str = ""


@dataclass
class CompactCommand(RpcCommand):
    type: str = "compact"
    instructions: str = ""


@dataclass
class GetStateCommand(RpcCommand):
    type: str = "get_state"


@dataclass
class CancelCommand(RpcCommand):
    type: str = "cancel"


@dataclass
class SetModelCommand(RpcCommand):
    type: str = "set_model"
    provider: str = ""
    model: str = ""


# --- Responses (server -> client) ---

@dataclass
class RpcResponse:
    """Base for all RPC responses."""
    command: str
    id: str = ""
    success: bool = True
    error: str = ""


@dataclass
class StateResponse(RpcResponse):
    command: str = "get_state"
    messages: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    total_cost: float = 0.0
    context_tokens: int = 0


# --- Events (server -> client, unsolicited) ---

@dataclass
class RpcEvent:
    """Base for all RPC events."""
    type: str


@dataclass
class MessageEvent(RpcEvent):
    type: str = "message"
    role: str = ""
    content: str = ""
    done: bool = False


@dataclass
class TextDeltaEvent(RpcEvent):
    type: str = "text_delta"
    content: str = ""


@dataclass
class ToolExecutionEvent(RpcEvent):
    type: str = "tool_execution"
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    result: str = ""
    success: bool = True
    phase: str = ""  # "start", "end"


@dataclass
class CompactionEvent(RpcEvent):
    type: str = "compaction"
    tokens_before: int = 0
    tokens_after: int = 0


@dataclass
class ErrorEvent(RpcEvent):
    type: str = "error"
    message: str = ""
```

#### `chimera/rpc/server.py`

```python
class RpcServer:
    """JSON-line RPC server over stdin/stdout.

    Reads one JSON command per line from stdin.
    Writes JSON responses and events to stdout (one per line).

    Args:
        agent: The Agent instance to control.
        env: Execution environment.
    """

    def __init__(self, agent: Agent, env: Environment | None = None) -> None:
        self._session: Session  # Created on first prompt
        self._agent = agent
        self._env = env

    def run(self) -> None:
        """Main loop: read commands, dispatch, emit responses."""
        import sys
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                command = self._parse_command(raw)
                self._dispatch(command)
            except Exception as e:
                self._emit(ErrorEvent(message=str(e)))

    def _dispatch(self, command: RpcCommand) -> None:
        """Route command to handler."""
        handler = self._handlers.get(command.type)
        if handler is None:
            self._emit(RpcResponse(command=command.type, id=command.id,
                                   success=False, error=f"Unknown command: {command.type}"))
            return
        handler(command)

    def _emit(self, event_or_response: RpcEvent | RpcResponse) -> None:
        """Write JSON line to stdout."""
        import sys
        sys.stdout.write(json.dumps(asdict(event_or_response)) + "\n")
        sys.stdout.flush()
```

#### `chimera/rpc/handler.py`

```python
from __future__ import annotations
from typing import Any, Callable
from chimera.rpc.types import *


class RpcHandler:
    """Maps RPC commands to agent/session operations.

    Each handler method processes one command type and emits
    responses/events via the emit callback.
    """

    def __init__(self, server: RpcServer) -> None:
        self._server = server

    def handle_prompt(self, cmd: PromptCommand) -> None:
        """Run a prompt through the session, emitting streaming events."""
        session = self._server._session

        # Stream events via iter_chat
        gen = session.iter_chat(cmd.message)
        try:
            for step in gen:
                # Emit tool execution events
                if step.tool_calls:
                    for i, tc in enumerate(step.tool_calls):
                        self._server._emit(ToolExecutionEvent(
                            tool_name=tc.name,
                            arguments=tc.arguments,
                            phase="start",
                        ))
                        if step.tool_results and i < len(step.tool_results):
                            tr = step.tool_results[i]
                            self._server._emit(ToolExecutionEvent(
                                tool_name=tc.name,
                                result=tr.output[:2000],
                                success=tr.success,
                                phase="end",
                            ))

                if step.done:
                    self._server._emit(MessageEvent(
                        role="assistant",
                        content=step.message.content,
                        done=True,
                    ))
        except Exception as e:
            self._server._emit(ErrorEvent(message=str(e)))
            return

        self._server._emit(RpcResponse(command="prompt", id=cmd.id))

    def handle_steer(self, cmd: SteerCommand) -> None:
        """Inject a steering message into the running turn."""
        self._server._session.steer(cmd.message)
        self._server._emit(RpcResponse(command="steer", id=cmd.id))

    def handle_cancel(self, cmd: CancelCommand) -> None:
        """Cancel the running agent turn."""
        self._server._session.cancel()
        self._server._emit(RpcResponse(command="cancel", id=cmd.id))

    def handle_get_state(self, cmd: GetStateCommand) -> None:
        """Return current session state."""
        session = self._server._session
        self._server._emit(StateResponse(
            id=cmd.id,
            messages=[{"role": m.role, "content": m.content}
                      for m in session.messages],
            model=session._agent.provider.model_name,
        ))

    def handle_compact(self, cmd: CompactCommand) -> None:
        """Trigger context compaction."""
        # Delegate to session compaction
        self._server._emit(RpcResponse(command="compact", id=cmd.id))

    @property
    def handlers(self) -> dict[str, Callable]:
        return {
            "prompt": self.handle_prompt,
            "steer": self.handle_steer,
            "cancel": self.handle_cancel,
            "get_state": self.handle_get_state,
            "compact": self.handle_compact,
        }
```

#### Protocol semantics

- **Request/response correlation**: Commands with `id` fields get responses with matching `id`. Events are unsolicited (no `id`).
- **Concurrent commands**: Commands are processed sequentially. A `prompt` command blocks until the turn completes. `steer` and `cancel` are injected asynchronously via message queues / cancellation tokens.
- **Error handling**: Unknown commands get an error response. Exceptions during handler execution emit `ErrorEvent` followed by an error response.

#### CLI integration

```bash
chimera code --mode rpc       # Start RPC server
chimera code --mode json      # JSON output mode (events only, no commands)
```

### Files Changed

| File | Change |
|------|--------|
| `chimera/rpc/__init__.py` | **New** |
| `chimera/rpc/types.py` | **New** — command/response/event types |
| `chimera/rpc/server.py` | **New** — RPC server |
| `chimera/rpc/handler.py` | **New** — command handlers |
| `chimera/cli/code.py` | Add `--mode rpc` and `--mode json` flags |
| `tests/test_rpc.py` | **New** |

### Backward Compatibility

Entirely additive. No existing behavior changes. The `--mode` flag defaults to `interactive` (current behavior).

---

## Dependency Graph

These features are largely independent but have some logical ordering:

```
Feature 1: Message Queues     ←── standalone (LoopConfig)
Feature 2: Provider Registry  ←── standalone (providers/)
Feature 3: Tool Operations    ←── standalone (tools/)
Feature 4: Session Tree       ←── standalone (sessions/)
Feature 5: File Tracking      ←── needs LoopConfig field (small dep on Feature 1's pattern)
Feature 6: Cancellation       ←── needs LoopConfig field, tool signature change
Feature 7: RPC Mode           ←── benefits from Features 1 + 6 (steering, cancel)
```

**Recommended implementation order:**

1. **Feature 2: Provider Registry** — smallest, zero risk, enables plugin providers
2. **Feature 5: File Tracking** — small, high value, standalone
3. **Feature 6: Cancellation** — foundational for interactive use
4. **Feature 1: Message Queues** — needs cancellation for clean steering
5. **Feature 3: Tool Operations** — larger refactor, backward-compat sensitive
6. **Feature 4: Session Tree** — largest new module, can be built in parallel with 3
7. **Feature 7: RPC Mode** — depends on 1 + 6 for full value

## Test Strategy

Each feature gets its own test file. All features are backward-compatible by design — existing tests must continue to pass unchanged. New tests cover:

- Unit tests for each new module (no LLM needed)
- Integration tests that wire features into LoopConfig and run mock agents
- For Feature 7 (RPC), subprocess-based integration tests

## Non-Goals

- **Not changing the Environment ABC.** Tool Operations (Feature 3) is additive — `Environment` continues to work as before.
- **Not adding new providers.** Feature 2 makes it easy to add providers, but the actual additions are separate work.
- **Not building a TUI.** Pi's `pi-tui` is impressive but Chimera's CLI is fine for now.
- **Not adding OAuth flows.** Chimera already has `chimera/auth/`.
