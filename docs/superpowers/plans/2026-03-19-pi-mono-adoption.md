# Pi-Mono Pattern Adoption — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt 7 architectural patterns from pi-mono into Chimera's stack — file tracking, provider registry, cancellation, message queues, tool operations, session tree, and RPC mode.

**Architecture:** Each feature is a standalone block injected via `LoopConfig` or new modules. All features are backward-compatible — existing APIs continue unchanged when new fields are `None`. Features follow TDD: write failing test → implement → verify → commit.

**Tech Stack:** Python 3.11+, pytest, no new dependencies (stdlib only).

**Spec:** `docs/superpowers/specs/2026-03-19-pi-mono-adoption-design.md`

---

## File Map

### New Files

| File | Feature | Responsibility |
|------|---------|---------------|
| `chimera/core/file_tracker.py` | 5 | Track files read/modified across compaction |
| `chimera/providers/registry.py` | 2 | Provider factory registry |
| `chimera/core/cancellation.py` | 6 | CancellationToken + CancellableTool mixin |
| `chimera/core/message_queue.py` | 1 | Thread-safe steering + follow-up queues |
| `chimera/core/operations.py` | 3 | Protocol interfaces for per-tool backends |
| `chimera/sessions/tree.py` | 4 | JSONL session persistence with branching |
| `chimera/rpc/__init__.py` | 7 | RPC package |
| `chimera/rpc/types.py` | 7 | RPC command/response/event dataclasses |
| `chimera/rpc/server.py` | 7 | stdin/stdout JSON-RPC server |
| `chimera/rpc/handler.py` | 7 | Command handlers |
| `tests/test_file_tracker.py` | 5 | Tests |
| `tests/test_provider_registry.py` | 2 | Tests |
| `tests/test_cancellation.py` | 6 | Tests |
| `tests/test_message_queue.py` | 1 | Tests |
| `tests/test_operations.py` | 3 | Tests |
| `tests/test_session_tree.py` | 4 | Tests |
| `tests/test_rpc.py` | 7 | Tests |

### Modified Files

| File | Features | Change Summary |
|------|----------|---------------|
| `chimera/core/loop_config.py` | 1,5,6 | Add `message_queues`, `file_tracker`, `cancellation` fields |
| `chimera/core/loop.py` | 1,6 | Steering drain, follow-up loop, cancellation checks |
| `chimera/core/tool_executor.py` | 5,6 | File tracking in tool exec, cancellation checks + bind |
| `chimera/core/tool.py` | 6 | Add `CancellableTool` mixin |
| `chimera/core/tool_group.py` | 3 | `create_default_tools()` accepting ops |
| `chimera/providers/factory.py` | 2 | Use registry, add `_ensure_builtins_registered()` |
| `chimera/providers/anthropic.py` | 2 | Self-register |
| `chimera/providers/openai_provider.py` | 2 | Self-register |
| `chimera/providers/google.py` | 2 | Self-register |
| `chimera/providers/ollama.py` | 2 | Self-register |
| `chimera/providers/compatible.py` | 2 | Self-register |
| `chimera/providers/modal.py` | 2 | Self-register |
| `chimera/compaction/base.py` | 5 | Add `CompactionMetadata`, `FileAwareCompaction` |
| `chimera/compaction/summary.py` | 5 | Extend `FileAwareCompaction`, include files in prompt |
| `chimera/sessions/session.py` | 1,4,6 | Add `steer()`, `queue()`, `cancel()`, `tree` param |
| `chimera/tools/read.py` | 3 | Accept optional `ReadOps` |
| `chimera/tools/write.py` | 3 | Accept optional `WriteOps` |
| `chimera/tools/bash.py` | 3,6 | Accept optional `BashOps`, extend `CancellableTool` |
| `chimera/tools/edit.py` | 3 | Accept optional `ReadOps` + `WriteOps` |
| `chimera/tools/search.py` | 3 | Accept optional `SearchOps` |
| `chimera/tools/list_files.py` | 3 | Accept optional `SearchOps` |
| `chimera/cli/code.py` | 4,7 | Add `/tree`, `/branch`, `/switch`, `--mode` flag |

---

## Chunk 1: File Tracking + Provider Registry

### Task 1: File Tracker

**Files:**
- Create: `chimera/core/file_tracker.py`
- Create: `tests/test_file_tracker.py`
- Modify: `chimera/compaction/base.py`

- [ ] **Step 1: Write failing tests for FileTracker**

Create `tests/test_file_tracker.py`:

```python
"""Tests for chimera.core.file_tracker."""
from chimera.core.file_tracker import FileTracker


def test_record_read():
    ft = FileTracker()
    ft.record_read("src/main.py")
    assert ft.read_files == ["src/main.py"]


def test_record_modified():
    ft = FileTracker()
    ft.record_modified("src/main.py")
    assert ft.modified_files == ["src/main.py"]


def test_dedup_reads():
    ft = FileTracker()
    ft.record_read("a.py")
    ft.record_read("a.py")
    ft.record_read("b.py")
    assert ft.read_files == ["a.py", "b.py"]


def test_dedup_modifications():
    ft = FileTracker()
    ft.record_modified("a.py")
    ft.record_modified("a.py")
    assert ft.modified_files == ["a.py"]


def test_to_prompt_section_empty():
    ft = FileTracker()
    assert ft.to_prompt_section() == ""


def test_to_prompt_section_with_files():
    ft = FileTracker()
    ft.record_read("a.py")
    ft.record_modified("b.py")
    section = ft.to_prompt_section()
    assert "Modified: b.py" in section
    assert "Read: a.py" in section


def test_to_metadata():
    ft = FileTracker()
    ft.record_read("a.py")
    ft.record_modified("b.py")
    meta = ft.to_metadata()
    assert meta.read_files == ["a.py"]
    assert meta.modified_files == ["b.py"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_file_tracker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chimera.core.file_tracker'`

- [ ] **Step 3: Write CompactionMetadata in compaction/base.py**

Add at the end of `chimera/compaction/base.py` (after the existing `CompactionView` class):

```python
@dataclass
class CompactionMetadata:
    """Metadata preserved across compaction boundaries."""

    read_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0

    def merge(self, other: CompactionMetadata) -> CompactionMetadata:
        """Merge another metadata, deduplicating file lists."""
        return CompactionMetadata(
            read_files=list(dict.fromkeys(self.read_files + other.read_files)),
            modified_files=list(dict.fromkeys(self.modified_files + other.modified_files)),
            tokens_before=other.tokens_before,
            tokens_after=other.tokens_after,
        )

    def to_prompt_section(self) -> str:
        """Generate prompt section listing tracked files."""
        if not self.read_files and not self.modified_files:
            return ""
        lines = ["## Files you've been working with"]
        if self.modified_files:
            lines.append("Modified: " + ", ".join(self.modified_files))
        if self.read_files:
            lines.append("Read: " + ", ".join(self.read_files))
        return "\n".join(lines)
```

Add the `field` import to the existing imports at the top of the file if not already present:
```python
from dataclasses import dataclass, field
```

- [ ] **Step 4: Write FileTracker implementation**

Create `chimera/core/file_tracker.py`:

```python
"""Track files read and modified during agent execution."""
from __future__ import annotations

from dataclasses import dataclass, field

from chimera.compaction.base import CompactionMetadata


@dataclass
class FileTracker:
    """Tracks files read and modified during agent execution.

    Thread-safe is not required — the tool executor runs tools
    sequentially within a single turn.
    """

    read_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    _seen_read: set[str] = field(default_factory=set)
    _seen_modified: set[str] = field(default_factory=set)

    def record_read(self, path: str) -> None:
        """Record a file read (deduplicated)."""
        if path not in self._seen_read:
            self._seen_read.add(path)
            self.read_files.append(path)

    def record_modified(self, path: str) -> None:
        """Record a file modification (deduplicated)."""
        if path not in self._seen_modified:
            self._seen_modified.add(path)
            self.modified_files.append(path)

    def to_metadata(self) -> CompactionMetadata:
        """Convert to CompactionMetadata for use during compaction."""
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

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_file_tracker.py -v`
Expected: 7 passed

- [ ] **Step 6: Wire FileTracker into tool executor**

In `chimera/core/tool_executor.py`, add at the top (after `_FILE_MODIFYING_TOOLS`):

```python
_FILE_READING_TOOLS = frozenset({"read_file"})
```

In `execute_tool_calls_incremental()`, after the `# -- Event: tool result --` block (around line 297), add:

```python
        # -- File tracking --
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

Add the same block to `async_execute_tool_calls_incremental()` (after the tool result event block, around line 440).

- [ ] **Step 7: Add `file_tracker` to LoopConfig**

In `chimera/core/loop_config.py`, add the import under `TYPE_CHECKING`:

```python
    from chimera.core.file_tracker import FileTracker
```

Add the field to the `LoopConfig` dataclass:

```python
    file_tracker: FileTracker | None = None
```

- [ ] **Step 8: Run full test suite**

Run: `uv run pytest tests/test_file_tracker.py tests/test_tool_execution.py tests/test_loop.py -v`
Expected: All pass

- [ ] **Step 9: Commit**

```bash
git add chimera/core/file_tracker.py chimera/compaction/base.py chimera/core/tool_executor.py chimera/core/loop_config.py tests/test_file_tracker.py
git commit -m "feat: file tracker — track read/modified files across compaction"
```

---

### Task 2: FileAwareCompaction mixin for SummaryCompaction

**Files:**
- Modify: `chimera/compaction/base.py`
- Modify: `chimera/compaction/summary.py`
- Create: `tests/test_file_aware_compaction.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_file_aware_compaction.py`:

```python
"""Tests for FileAwareCompaction mixin."""
from chimera.compaction.base import CompactionMetadata, FileAwareCompaction
from chimera.compaction.summary import SummaryCompaction
from chimera.types import Message


def test_file_aware_compaction_is_mixin():
    """SummaryCompaction should be a FileAwareCompaction."""
    sc = SummaryCompaction()
    assert isinstance(sc, FileAwareCompaction)


def test_set_metadata():
    sc = SummaryCompaction()
    meta = CompactionMetadata(read_files=["a.py"], modified_files=["b.py"])
    sc.set_metadata(meta)
    section = sc.get_file_prompt_section()
    assert "a.py" in section
    assert "b.py" in section


def test_file_section_empty_without_metadata():
    sc = SummaryCompaction()
    assert sc.get_file_prompt_section() == ""


def test_compact_includes_files_in_summary():
    """When metadata is set, the summary should mention tracked files."""
    sc = SummaryCompaction(keep_first=1, keep_last=1)
    meta = CompactionMetadata(read_files=["src/app.py"], modified_files=["src/main.py"])
    sc.set_metadata(meta)

    messages = [
        Message.system("system"),
        Message.user("do stuff"),
        Message.assistant("ok"),
        Message.user("more stuff"),
        Message.assistant("done"),
    ]
    result = sc.compact(messages, budget=1000)
    # Summary message should contain file tracking info
    summary_msg = result[1]  # After system, before last kept
    assert "src/main.py" in summary_msg.content or "src/app.py" in summary_msg.content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_file_aware_compaction.py -v`
Expected: FAIL — `ImportError: cannot import name 'FileAwareCompaction'`

- [ ] **Step 3: Add FileAwareCompaction to compaction/base.py**

Add at the end of `chimera/compaction/base.py`:

```python
class FileAwareCompaction(CompactionStrategy):
    """Mixin for compaction strategies that use file tracking metadata.

    Subclasses gain ``set_metadata()`` / ``get_file_prompt_section()``
    for including file awareness in compacted summaries.
    """

    _file_metadata: CompactionMetadata | None = None

    def set_metadata(self, metadata: CompactionMetadata) -> None:
        """Inject file tracking metadata before compaction."""
        self._file_metadata = metadata

    def get_file_prompt_section(self) -> str:
        """Generate prompt section listing tracked files."""
        if self._file_metadata is None:
            return ""
        return self._file_metadata.to_prompt_section()
```

- [ ] **Step 4: Make SummaryCompaction extend FileAwareCompaction**

In `chimera/compaction/summary.py`, change the import:

```python
from chimera.compaction.base import CompactionStrategy, FileAwareCompaction
```

Change the class declaration:

```python
class SummaryCompaction(FileAwareCompaction):
```

Update `_summarize_simple` to include file tracking:

```python
    def _summarize_simple(self, messages: list[Message]) -> str:
        """Produce a human-readable count of messages by role."""
        counts: dict[str, int] = {}
        tool_calls = 0
        for msg in messages:
            counts[msg.role] = counts.get(msg.role, 0) + 1
            tool_calls += len(msg.tool_calls)

        parts: list[str] = []
        for role in ("user", "assistant", "system", "tool"):
            n = counts.get(role, 0)
            if n:
                parts.append(f"{n} {role} message{'s' if n != 1 else ''}")
        if tool_calls:
            parts.append(f"{tool_calls} tool call{'s' if tool_calls != 1 else ''}")

        summary = f"Summarized: {', '.join(parts)}." if parts else "Summarized conversation."

        file_section = self.get_file_prompt_section()
        if file_section:
            summary += "\n\n" + file_section

        return summary
```

Update `_summarize_with_provider` similarly — append file section to the prompt:

```python
    def _summarize_with_provider(self, messages: list[Message]) -> str:
        """Use the configured LLM provider to produce a summary."""
        conversation = "\n".join(
            f"[{m.role}] {m.content[:200]}" for m in messages
        )
        file_section = self.get_file_prompt_section()
        extra = f"\n\nFiles tracked:\n{file_section}" if file_section else ""
        prompt = (
            "Summarize the following conversation excerpt in a concise paragraph. "
            "Focus on key decisions, actions taken, and results.\n\n"
            f"{conversation}{extra}"
        )
        response = self._provider.complete(
            messages=[Message.user(prompt)],
            max_tokens=self.summary_max_tokens,
        )
        return response.content
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_file_aware_compaction.py tests/test_compaction.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add chimera/compaction/base.py chimera/compaction/summary.py tests/test_file_aware_compaction.py
git commit -m "feat: FileAwareCompaction mixin — file tracking in compaction summaries"
```

---

### Task 3: Provider Registry

**Files:**
- Create: `chimera/providers/registry.py`
- Create: `tests/test_provider_registry.py`
- Modify: `chimera/providers/factory.py`
- Modify: `chimera/providers/anthropic.py`
- Modify: `chimera/providers/openai_provider.py`
- Modify: `chimera/providers/google.py`
- Modify: `chimera/providers/ollama.py`
- Modify: `chimera/providers/compatible.py`
- Modify: `chimera/providers/modal.py`

- [ ] **Step 1: Write failing tests for registry**

Create `tests/test_provider_registry.py`:

```python
"""Tests for chimera.providers.registry."""
from chimera.providers.registry import (
    register_provider,
    get_provider_factory,
    list_providers,
    unregister_provider,
    _registry,
)
from chimera.providers.base import Provider, Response


class MockProvider(Provider):
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        return Response(content="mock", tool_calls=[], usage={})

    @property
    def context_window(self):
        return 1000

    @property
    def supports_tool_use(self):
        return True

    @property
    def model_name(self):
        return "mock"


def _mock_factory(model="mock", **kwargs):
    return MockProvider()


def test_register_and_get():
    register_provider("test-provider", _mock_factory)
    factory = get_provider_factory("test-provider")
    assert factory is _mock_factory
    unregister_provider("test-provider")


def test_get_unknown_returns_none():
    assert get_provider_factory("nonexistent-xyz") is None


def test_list_providers():
    register_provider("test-list", _mock_factory)
    assert "test-list" in list_providers()
    unregister_provider("test-list")


def test_unregister():
    register_provider("test-unreg", _mock_factory)
    unregister_provider("test-unreg")
    assert get_provider_factory("test-unreg") is None


def test_builtins_registered_after_ensure():
    """After _ensure_builtins_registered, built-in providers should be available."""
    from chimera.providers.registry import _ensure_builtins_registered
    _ensure_builtins_registered()
    names = list_providers()
    assert "anthropic" in names
    assert "openai" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_provider_registry.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create registry module**

Create `chimera/providers/registry.py`:

```python
"""Runtime provider registry for pluggable provider factories."""
from __future__ import annotations

from typing import Any, Callable

from chimera.providers.base import Provider

ProviderFactory = Callable[..., Provider]

_registry: dict[str, ProviderFactory] = {}
_builtins_registered = False


def register_provider(name: str, factory: ProviderFactory) -> None:
    """Register a provider factory by name."""
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


def _ensure_builtins_registered() -> None:
    """Import all built-in provider modules to trigger self-registration."""
    global _builtins_registered
    if _builtins_registered:
        return
    _builtins_registered = True
    import chimera.providers.anthropic  # noqa: F401
    import chimera.providers.openai_provider  # noqa: F401
    import chimera.providers.google  # noqa: F401
    import chimera.providers.ollama  # noqa: F401
    import chimera.providers.compatible  # noqa: F401
    import chimera.providers.modal  # noqa: F401
```

- [ ] **Step 4: Add self-registration to each provider module**

Append to the **end** of each provider file:

`chimera/providers/anthropic.py`:
```python
# Self-register with provider registry
from chimera.providers.registry import register_provider as _register
_register("anthropic", lambda model="", api_key=None, base_url=None, **kw: AnthropicProvider(model=model, api_key=api_key, base_url=base_url))
```

`chimera/providers/openai_provider.py`:
```python
from chimera.providers.registry import register_provider as _register
_register("openai", lambda model="", api_key=None, base_url=None, **kw: OpenAIProvider(model=model, api_key=api_key, base_url=base_url))
```

`chimera/providers/google.py`:
```python
from chimera.providers.registry import register_provider as _register
_register("google", lambda model="", api_key=None, **kw: GoogleProvider(model=model, api_key=api_key))
```

`chimera/providers/ollama.py`:
```python
from chimera.providers.registry import register_provider as _register
_register("ollama", lambda model="", base_url="http://localhost:11434", **kw: OllamaProvider(model=model, base_url=base_url, **kw))
```

`chimera/providers/compatible.py`:
```python
from chimera.providers.registry import register_provider as _register
_register("compatible", lambda model="", base_url=None, api_key=None, **kw: OpenAICompatibleProvider(model=model, base_url=base_url or "", api_key=api_key, **kw))
```

`chimera/providers/modal.py`:
```python
from chimera.providers.registry import register_provider as _register
_register("modal", lambda model="", base_url=None, **kw: ModalProvider(model=model, base_url=base_url, **kw))
```

- [ ] **Step 5: Refactor factory.py to use registry**

Replace the body of `create_provider()` in `chimera/providers/factory.py`:

```python
def create_provider(
    provider_type: str | None = None,
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs: Any,
) -> Provider:
    from chimera.providers.registry import (
        _ensure_builtins_registered,
        get_provider_factory,
        list_providers,
    )
    _ensure_builtins_registered()

    if model is None:
        import os
        model = os.environ.get("ANTHROPIC_MODEL") or os.environ.get("OPENAI_MODEL")
        if model is None:
            raise ValueError(
                "No model specified. Pass model= or set ANTHROPIC_MODEL / OPENAI_MODEL."
            )

    if provider_type is None:
        provider_type = _infer_provider(model)

    factory = get_provider_factory(provider_type)
    if factory is not None:
        return factory(model=model, api_key=api_key, base_url=base_url, **kwargs)

    raise ValueError(
        f"Unknown provider: '{provider_type}'. "
        f"Registered: {list_providers()}"
    )
```

Keep `_infer_provider()` unchanged.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_provider_registry.py tests/test_provider.py tests/test_providers.py -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add chimera/providers/registry.py chimera/providers/factory.py chimera/providers/anthropic.py chimera/providers/openai_provider.py chimera/providers/google.py chimera/providers/ollama.py chimera/providers/compatible.py chimera/providers/modal.py tests/test_provider_registry.py
git commit -m "feat: provider registry — runtime provider registration"
```

---

## Chunk 2: Cancellation + Message Queues

### Task 4: Cancellation Token

**Files:**
- Create: `chimera/core/cancellation.py`
- Create: `tests/test_cancellation.py`
- Modify: `chimera/core/loop_config.py`
- Modify: `chimera/core/tool.py`
- Modify: `chimera/core/tool_executor.py`
- Modify: `chimera/core/loop.py`
- Modify: `chimera/tools/bash.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_cancellation.py`:

```python
"""Tests for chimera.core.cancellation."""
import threading
import pytest
from chimera.core.cancellation import CancellationToken, OperationCancelled


def test_not_cancelled_by_default():
    token = CancellationToken()
    assert not token.is_cancelled


def test_cancel_sets_flag():
    token = CancellationToken()
    token.cancel()
    assert token.is_cancelled


def test_check_raises_when_cancelled():
    token = CancellationToken()
    token.cancel()
    with pytest.raises(OperationCancelled):
        token.check()


def test_check_does_nothing_when_not_cancelled():
    token = CancellationToken()
    token.check()  # Should not raise


def test_on_cancel_callback_immediate():
    token = CancellationToken()
    token.cancel()
    called = []
    token.on_cancel(lambda: called.append(True))
    assert called == [True]  # Called immediately since already cancelled


def test_on_cancel_callback_deferred():
    token = CancellationToken()
    called = []
    token.on_cancel(lambda: called.append(True))
    assert called == []
    token.cancel()
    assert called == [True]


def test_wait_returns_true_on_cancel():
    token = CancellationToken()
    threading.Timer(0.01, token.cancel).start()
    assert token.wait(timeout=1.0) is True


def test_wait_returns_false_on_timeout():
    token = CancellationToken()
    assert token.wait(timeout=0.01) is False


def test_cancellable_tool_mixin():
    from chimera.core.cancellation import CancellableTool
    from chimera.core.tool import BaseTool
    from chimera.types import ToolResult

    class DummyTool(CancellableTool):
        name = "dummy"
        description = "dummy"
        parameters = {"type": "object", "properties": {}}

        def execute(self, args, env):
            return ToolResult(output="ok")

    tool = DummyTool()
    assert tool._cancel_token is None
    token = CancellationToken()
    tool.bind_cancellation(token)
    assert tool._cancel_token is token
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cancellation.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write cancellation module**

Create `chimera/core/cancellation.py`:

```python
"""Cooperative cancellation token for agent operations."""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from chimera.core.tool import BaseTool


class OperationCancelled(Exception):
    """Raised when a cancellation token is checked after cancel()."""
    pass


class CancellationToken:
    """Cooperative cancellation token.

    Thread-safe. Any layer can check ``is_cancelled`` and any layer
    can call ``cancel()`` to signal all holders.
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
        """Whether cancellation has been requested."""
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


class CancellableTool:
    """Mixin for tools that support cooperative cancellation.

    The tool executor calls ``bind_cancellation()`` before executing
    tools that extend this mixin.
    """

    _cancel_token: CancellationToken | None = None

    def bind_cancellation(self, token: CancellationToken) -> None:
        """Bind a cancellation token to this tool."""
        self._cancel_token = token
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cancellation.py -v`
Expected: 9 passed

- [ ] **Step 5: Add `cancellation` to LoopConfig**

In `chimera/core/loop_config.py`, add the import under `TYPE_CHECKING`:

```python
    from chimera.core.cancellation import CancellationToken
```

Add the field to the `LoopConfig` dataclass:

```python
    cancellation: CancellationToken | None = None
```

- [ ] **Step 6: Wire cancellation checks into tool executor**

In `chimera/core/tool_executor.py`, in `execute_tool_calls_incremental()`, add at the very top of the `for i, tc in enumerate(tool_calls):` loop body (before the permission check):

```python
        # -- Cancellation check --
        if config and config.cancellation:
            config.cancellation.check()
```

And add `CancellableTool` binding just before `# -- Execute --`:

```python
        # -- Cancellation: bind token to cancellable tools --
        if config and config.cancellation:
            from chimera.core.cancellation import CancellableTool
            if isinstance(tool, CancellableTool):
                tool.bind_cancellation(config.cancellation)
```

Do the same in `async_execute_tool_calls_incremental()`: add cancellation check at top of the `for i, tc` loop, and bind cancellation before `approved.append(...)`.

- [ ] **Step 7: Add cancellation checks in ReAct loop**

In `chimera/core/loop.py`, in `iter_steps()`, add at the top of the `for _ in range(self.max_steps):` loop (line 73), before the `steps += 1`:

```python
            # -- Cancellation check --
            if self.config and self.config.cancellation:
                from chimera.core.cancellation import OperationCancelled
                try:
                    self.config.cancellation.check()
                except OperationCancelled:
                    yield StepResult(
                        message=Message.assistant("Operation cancelled"),
                        done=True, step=steps, cost=0.0,
                    )
                    return AgentResult(
                        output="Operation cancelled", steps=steps,
                        tool_calls_total=total_tool_calls, cost=total_cost,
                        success=False, error="Cancelled",
                    )
```

Do the same in `async_iter_steps()` at the top of its `for _ in range(self.max_steps):` loop.

- [ ] **Step 8: Run tests**

Run: `uv run pytest tests/test_cancellation.py tests/test_loop.py tests/test_tool_execution.py -v`
Expected: All pass

- [ ] **Step 9: Commit**

```bash
git add chimera/core/cancellation.py chimera/core/loop_config.py chimera/core/tool_executor.py chimera/core/loop.py tests/test_cancellation.py
git commit -m "feat: cancellation token — cooperative cancellation throughout agent loop"
```

---

### Task 5: Message Queues

**Files:**
- Create: `chimera/core/message_queue.py`
- Create: `tests/test_message_queue.py`
- Modify: `chimera/core/loop_config.py`
- Modify: `chimera/core/loop.py`
- Modify: `chimera/sessions/session.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_message_queue.py`:

```python
"""Tests for chimera.core.message_queue."""
from chimera.core.message_queue import MessageQueues
from chimera.types import Message


def test_empty_queues():
    q = MessageQueues()
    assert not q.has_steering
    assert not q.has_follow_up
    assert q.drain_steering() == []
    assert q.drain_follow_up() == []


def test_steer():
    q = MessageQueues()
    q.steer(Message.user("change direction"))
    assert q.has_steering
    msgs = q.drain_steering()
    assert len(msgs) == 1
    assert msgs[0].content == "change direction"
    assert not q.has_steering  # Drained


def test_follow_up():
    q = MessageQueues()
    q.follow_up(Message.user("next task"))
    assert q.has_follow_up
    msgs = q.drain_follow_up()
    assert len(msgs) == 1
    assert msgs[0].content == "next task"
    assert not q.has_follow_up


def test_multiple_steering():
    q = MessageQueues()
    q.steer(Message.user("a"))
    q.steer(Message.user("b"))
    msgs = q.drain_steering()
    assert [m.content for m in msgs] == ["a", "b"]


def test_thread_safety():
    """Basic thread safety test — steer from another thread."""
    import threading
    q = MessageQueues()

    def _steer():
        q.steer(Message.user("from thread"))

    t = threading.Thread(target=_steer)
    t.start()
    t.join()
    assert q.has_steering
    assert q.drain_steering()[0].content == "from thread"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_message_queue.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write MessageQueues implementation**

Create `chimera/core/message_queue.py`:

```python
"""Thread-safe steering and follow-up message queues."""
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

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_message_queue.py -v`
Expected: 5 passed

- [ ] **Step 5: Add `message_queues` to LoopConfig**

In `chimera/core/loop_config.py`, add the import under `TYPE_CHECKING`:

```python
    from chimera.core.message_queue import MessageQueues
```

Add the field:

```python
    message_queues: MessageQueues | None = None
```

- [ ] **Step 6: Wire steering into ReAct loop**

In `chimera/core/loop.py`, in `iter_steps()`, add after the tool execution block — right before `if handler: handler.on_step_end(steps)` at the end of the `for` loop body (approximately line 258):

```python
            # -- Drain steering messages --
            if self.config and self.config.message_queues:
                for msg in self.config.message_queues.drain_steering():
                    context.add(msg)
```

Add the same block in `async_iter_steps()` at the equivalent position.

- [ ] **Step 7: Wire follow-up loop into ReAct.run()**

In `chimera/core/loop.py`, replace the `run()` method body:

```python
    def run(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> AgentResult:
        """Run the loop to completion, auto-denying ASK permissions."""
        from chimera.core.middleware import MiddlewareChain

        while True:
            result = drain_steps(self.iter_steps(provider, tools, context, env))
            mw_chain = MiddlewareChain(self.config.middleware if self.config else None)
            result = mw_chain.run_after_agent(result, env)

            # Follow-up loop: process queued messages
            if (self.config and self.config.message_queues
                    and self.config.message_queues.has_follow_up):
                for msg in self.config.message_queues.drain_follow_up():
                    context.add(msg)
                continue
            break

        return result
```

- [ ] **Step 8: Add steer/queue to Session**

In `chimera/sessions/session.py`, add two methods to the `Session` class:

```python
    def steer(self, message: str) -> None:
        """Inject a steering message into the running turn."""
        config = getattr(self._agent.loop, "config", None)
        if config and config.message_queues:
            config.message_queues.steer(Message.user(message))

    def queue(self, message: str) -> None:
        """Queue a follow-up message for after the current turn."""
        config = getattr(self._agent.loop, "config", None)
        if config and config.message_queues:
            config.message_queues.follow_up(Message.user(message))
```

- [ ] **Step 9: Run tests**

Run: `uv run pytest tests/test_message_queue.py tests/test_loop.py tests/test_session.py -v`
Expected: All pass

- [ ] **Step 10: Commit**

```bash
git add chimera/core/message_queue.py chimera/core/loop_config.py chimera/core/loop.py chimera/sessions/session.py tests/test_message_queue.py
git commit -m "feat: message queues — steering and follow-up for interactive agents"
```

---

## Chunk 3: Tool Operations

### Task 6: Operations Protocols + Local Defaults

**Files:**
- Create: `chimera/core/operations.py`
- Create: `tests/test_operations.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_operations.py`:

```python
"""Tests for chimera.core.operations."""
import os
import tempfile
from chimera.core.operations import (
    ReadOps, WriteOps, BashOps, SearchOps,
    LocalReadOps, LocalWriteOps, LocalBashOps, LocalSearchOps,
)


def test_local_read_ops(tmp_path):
    (tmp_path / "hello.txt").write_text("hello world")
    ops = LocalReadOps(cwd=str(tmp_path))
    assert ops.read_file("hello.txt") == "hello world"


def test_local_read_ops_absolute(tmp_path):
    f = tmp_path / "abs.txt"
    f.write_text("absolute")
    ops = LocalReadOps(cwd="/tmp")
    assert ops.read_file(str(f)) == "absolute"


def test_local_read_ops_file_exists(tmp_path):
    (tmp_path / "exists.txt").write_text("yes")
    ops = LocalReadOps(cwd=str(tmp_path))
    assert ops.file_exists("exists.txt")
    assert not ops.file_exists("nope.txt")


def test_local_write_ops(tmp_path):
    ops = LocalWriteOps(cwd=str(tmp_path))
    ops.write_file("out.txt", "content")
    assert (tmp_path / "out.txt").read_text() == "content"


def test_local_write_ops_creates_dirs(tmp_path):
    ops = LocalWriteOps(cwd=str(tmp_path))
    ops.write_file("sub/dir/file.txt", "nested")
    assert (tmp_path / "sub" / "dir" / "file.txt").read_text() == "nested"


def test_local_bash_ops(tmp_path):
    ops = LocalBashOps(cwd=str(tmp_path))
    result = ops.run_command("echo hello")
    assert result.exit_code == 0
    assert "hello" in result.stdout


def test_local_bash_ops_timeout(tmp_path):
    ops = LocalBashOps(cwd=str(tmp_path))
    result = ops.run_command("sleep 10", timeout=1)
    assert result.exit_code != 0 or "Timeout" in result.stderr


def test_local_search_ops(tmp_path):
    (tmp_path / "a.py").write_text("def foo(): pass")
    (tmp_path / "b.txt").write_text("hello")
    ops = LocalSearchOps(cwd=str(tmp_path))
    files = ops.list_files("**/*")
    assert "a.py" in files or str(tmp_path / "a.py") in str(files)


def test_protocol_compliance():
    """Local ops should satisfy their protocol."""
    assert isinstance(LocalReadOps(), ReadOps)
    assert isinstance(LocalWriteOps(), WriteOps)
    assert isinstance(LocalBashOps(), BashOps)
    assert isinstance(LocalSearchOps(), SearchOps)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_operations.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write operations module**

Create `chimera/core/operations.py`:

```python
"""Protocol interfaces for pluggable tool backends."""
from __future__ import annotations

import glob as globmod
import os
import subprocess
from pathlib import Path
from typing import Protocol, runtime_checkable

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
    """Backend for file search."""

    def search_files(self, pattern: str, path: str = ".") -> list[str]: ...
    def list_files(self, pattern: str = "**/*") -> list[str]: ...


# -- Local implementations --


class LocalReadOps:
    """ReadOps using local filesystem."""

    def __init__(self, cwd: str = ".") -> None:
        self.cwd = cwd

    def read_file(self, path: str) -> str:
        full = path if os.path.isabs(path) else os.path.join(self.cwd, path)
        with open(full) as f:
            return f.read()

    def file_exists(self, path: str) -> bool:
        full = path if os.path.isabs(path) else os.path.join(self.cwd, path)
        return os.path.exists(full)


class LocalWriteOps:
    """WriteOps using local filesystem."""

    def __init__(self, cwd: str = ".") -> None:
        self.cwd = cwd

    def write_file(self, path: str, content: str) -> None:
        full = path if os.path.isabs(path) else os.path.join(self.cwd, path)
        Path(full).parent.mkdir(parents=True, exist_ok=True)
        with open(full, "w") as f:
            f.write(content)


class LocalBashOps:
    """BashOps using subprocess."""

    def __init__(self, cwd: str = ".") -> None:
        self.cwd = cwd

    def run_command(self, command: str, timeout: int = 120,
                    cwd: str | None = None) -> CommandResult:
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


class LocalSearchOps:
    """SearchOps using local filesystem."""

    def __init__(self, cwd: str = ".") -> None:
        self.cwd = cwd

    def list_files(self, pattern: str = "**/*") -> list[str]:
        base = Path(self.cwd)
        return [
            str(p.relative_to(base))
            for p in base.glob(pattern)
            if p.is_file()
        ]

    def search_files(self, pattern: str, path: str = ".") -> list[str]:
        import re
        regex = re.compile(pattern)
        results: list[str] = []
        search_dir = path if os.path.isabs(path) else os.path.join(self.cwd, path)
        for filepath in Path(search_dir).rglob("*"):
            if not filepath.is_file():
                continue
            try:
                content = filepath.read_text()
            except (UnicodeDecodeError, PermissionError):
                continue
            for i, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    rel = str(filepath.relative_to(self.cwd)) if not os.path.isabs(path) else str(filepath)
                    results.append(f"{rel}:{i}: {line}")
        return results
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_operations.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add chimera/core/operations.py tests/test_operations.py
git commit -m "feat: tool operations protocols + local implementations"
```

---

### Task 7: Wire Operations into Tools + Tool Group

**Files:**
- Modify: `chimera/tools/read.py`
- Modify: `chimera/tools/write.py`
- Modify: `chimera/tools/bash.py`
- Modify: `chimera/tools/edit.py`
- Modify: `chimera/tools/search.py`
- Modify: `chimera/tools/list_files.py`
- Modify: `chimera/core/tool_group.py`

- [ ] **Step 1: Refactor ReadFileTool**

Replace `chimera/tools/read.py`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.core.operations import ReadOps


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read the contents of a file."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the file"},
        },
        "required": ["path"],
    }

    def __init__(self, ops: ReadOps | None = None) -> None:
        self._ops = ops

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        path = args["path"]
        if self._ops is not None:
            try:
                content = self._ops.read_file(path)
                return ToolResult(output=content)
            except FileNotFoundError:
                return ToolResult(output="", error=f"File not found: {path}")
        assert env is not None
        try:
            content = env.read_file(path)
            return ToolResult(output=content)
        except FileNotFoundError:
            return ToolResult(output="", error=f"File not found: {path}")
```

- [ ] **Step 2: Refactor WriteFileTool**

Replace `chimera/tools/write.py`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ChangeType, FileChange, ToolResult

if TYPE_CHECKING:
    from chimera.core.operations import ReadOps, WriteOps


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write content to a file. Creates parent directories if needed."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the file"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    }

    def __init__(self, read_ops: ReadOps | None = None,
                 write_ops: WriteOps | None = None) -> None:
        self._read_ops = read_ops
        self._write_ops = write_ops

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        path = args["path"]
        new_content = args["content"]

        # Read before-state for diff
        before: str | None = None
        try:
            if self._read_ops is not None:
                before = self._read_ops.read_file(path)
            elif env is not None:
                before = env.read_file(path)
        except (FileNotFoundError, OSError):
            pass

        try:
            if self._write_ops is not None:
                self._write_ops.write_file(path, new_content)
            elif env is not None:
                env.write_file(path, new_content)
            else:
                return ToolResult(output="", error="No write backend available")
        except Exception as e:
            return ToolResult(output="", error=str(e))

        change_type = ChangeType.CREATE if before is None else ChangeType.EDIT
        fc = FileChange(
            path=path, change_type=change_type,
            before_content=before, after_content=new_content,
            diff=FileChange.compute_diff(path, before or "", new_content),
        )
        return ToolResult(output=f"Written to {path}", metadata={"file_change": fc})
```

- [ ] **Step 3: Refactor BashTool (also add CancellableTool)**

Replace `chimera/tools/bash.py`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chimera.core.cancellation import CancellableTool
from chimera.env.base import Environment
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.core.operations import BashOps


class BashTool(CancellableTool):
    name = "bash"
    description = "Execute a shell command and return its output."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 120},
        },
        "required": ["command"],
    }

    def __init__(self, ops: BashOps | None = None) -> None:
        self._ops = ops

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        timeout = args.get("timeout", 120)
        command = args["command"]

        if self._ops is not None:
            result = self._ops.run_command(command, timeout=timeout)
        elif env is not None:
            result = env.run_command(command, timeout=timeout)
        else:
            return ToolResult(output="", error="No bash backend available")

        output = result.stdout
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"
        if result.success:
            return ToolResult(output=output)
        else:
            return ToolResult(output=output, error=f"Exit code {result.exit_code}")
```

- [ ] **Step 4: Refactor EditFileTool**

In `chimera/tools/edit.py`, update the constructor to accept ops:

```python
class EditFileTool(BaseTool):
    # ... name, description, parameters unchanged ...

    def __init__(self, editor: FuzzyEditor | None = None,
                 read_ops: ReadOps | None = None,
                 write_ops: WriteOps | None = None) -> None:
        self._editor = editor
        self._read_ops = read_ops
        self._write_ops = write_ops

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        path = args["path"]
        try:
            if self._read_ops is not None:
                content = self._read_ops.read_file(path)
            elif env is not None:
                content = env.read_file(path)
            else:
                return ToolResult(output="", error="No read backend available")
        except FileNotFoundError:
            return ToolResult(output="", error=f"File not found: {path}")

        old = args["old_string"]
        new = args["new_string"]
        count = content.count(old)

        if count == 1:
            updated = content.replace(old, new, 1)
            match_strategy = "exact"
        elif self._editor is not None:
            result = self._editor.find(content, old)
            if result is None:
                return ToolResult(output="", error=f"String not found in {path} (tried fuzzy matching)")
            updated = content[:result.start] + new + content[result.end:]
            match_strategy = result.strategy_name
        elif count == 0:
            return ToolResult(output="", error=f"String not found in {path}")
        else:
            return ToolResult(output="", error=f"Multiple matches ({count}) found — ambiguous. Provide more context.")

        if self._write_ops is not None:
            self._write_ops.write_file(path, updated)
        elif env is not None:
            env.write_file(path, updated)
        else:
            return ToolResult(output="", error="No write backend available")

        fc = FileChange(
            path=path, change_type=ChangeType.EDIT,
            before_content=content, after_content=updated,
            diff=FileChange.compute_diff(path, content, updated),
        )
        return ToolResult(
            output=f"Edited {path}",
            metadata={"file_change": fc, "match_strategy": match_strategy},
        )
```

Add `TYPE_CHECKING` imports at top:
```python
if TYPE_CHECKING:
    from chimera.core.operations import ReadOps, WriteOps
    from chimera.tools.strategies import FuzzyEditor
```

- [ ] **Step 5: Refactor SearchTool and ListFilesTool**

In `chimera/tools/search.py`, add ops support:

```python
class SearchTool(BaseTool):
    # ... name, description, parameters unchanged ...

    def __init__(self, ops: SearchOps | None = None) -> None:
        self._ops = ops

    def execute(self, args, env):
        # ... if self._ops: use self._ops.search_files()
        # ... else: existing env-based logic ...
```

In `chimera/tools/list_files.py`, same pattern:

```python
class ListFilesTool(BaseTool):
    def __init__(self, ops: SearchOps | None = None) -> None:
        self._ops = ops
```

(Keep existing `execute()` logic as fallback when `self._ops is None`.)

- [ ] **Step 6: Update tool_group.py**

In `chimera/core/tool_group.py`, add a factory function:

```python
def create_default_tools(
    read_ops: ReadOps | None = None,
    write_ops: WriteOps | None = None,
    bash_ops: BashOps | None = None,
    search_ops: SearchOps | None = None,
) -> ToolGroup:
    """Create default tool set with optional operation backends."""
    from chimera.tools.read import ReadFileTool
    from chimera.tools.write import WriteFileTool
    from chimera.tools.bash import BashTool
    from chimera.tools.image_read import ImageReadTool
    return ToolGroup("default", [
        ReadFileTool(ops=read_ops),
        WriteFileTool(read_ops=read_ops, write_ops=write_ops),
        BashTool(ops=bash_ops),
        ImageReadTool(),
    ])
```

Add `TYPE_CHECKING` imports:
```python
if TYPE_CHECKING:
    from chimera.core.operations import ReadOps, WriteOps, BashOps, SearchOps
```

- [ ] **Step 7: Run full test suite**

Run: `uv run pytest tests/ -x --timeout=60`
Expected: All existing tests pass (tools still work with env)

- [ ] **Step 8: Commit**

```bash
git add chimera/tools/read.py chimera/tools/write.py chimera/tools/bash.py chimera/tools/edit.py chimera/tools/search.py chimera/tools/list_files.py chimera/core/tool_group.py tests/test_operations.py
git commit -m "feat: pluggable tool operations — per-tool backend protocols"
```

---

## Chunk 4: Session Tree

### Task 8: SessionTree Core

**Files:**
- Create: `chimera/sessions/tree.py`
- Create: `tests/test_session_tree.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_session_tree.py`:

```python
"""Tests for chimera.sessions.tree."""
import json
from chimera.sessions.tree import SessionTree, SessionHeader, MessageEntry
from chimera.types import Message, ToolCall


def test_create_empty_tree(tmp_path):
    path = tmp_path / "session.jsonl"
    tree = SessionTree(path)
    assert tree.entry_count == 0
    assert tree.active_leaf is None


def test_add_message(tmp_path):
    path = tmp_path / "session.jsonl"
    tree = SessionTree(path)
    entry_id = tree.add_message(Message.user("hello"))
    assert tree.entry_count == 1
    assert tree.active_leaf == entry_id


def test_get_messages(tmp_path):
    path = tmp_path / "session.jsonl"
    tree = SessionTree(path)
    tree.add_message(Message.user("hello"))
    tree.add_message(Message.assistant("hi"))
    msgs = tree.get_messages()
    assert len(msgs) == 2
    assert msgs[0].content == "hello"
    assert msgs[1].content == "hi"


def test_persistence(tmp_path):
    path = tmp_path / "session.jsonl"
    tree1 = SessionTree(path)
    tree1.add_message(Message.user("hello"))
    tree1.add_message(Message.assistant("hi"))

    # Reload from disk
    tree2 = SessionTree(path)
    assert tree2.entry_count == 2
    msgs = tree2.get_messages()
    assert msgs[0].content == "hello"
    assert msgs[1].content == "hi"


def test_fork_and_branch(tmp_path):
    path = tmp_path / "session.jsonl"
    tree = SessionTree(path)
    id1 = tree.add_message(Message.user("hello"))
    id2 = tree.add_message(Message.assistant("hi"))
    id3 = tree.add_message(Message.user("continue"))

    # Fork from id2
    tree.fork(id2)
    id4 = tree.add_message(Message.user("different path"))

    # Branch from id2 should show: hello, hi, different path
    msgs = tree.get_messages()
    assert len(msgs) == 3
    assert msgs[2].content == "different path"


def test_get_leaves(tmp_path):
    path = tmp_path / "session.jsonl"
    tree = SessionTree(path)
    id1 = tree.add_message(Message.user("hello"))
    id2 = tree.add_message(Message.assistant("hi"))

    # Fork from id1
    tree.fork(id1)
    id3 = tree.add_message(Message.user("branch"))

    leaves = tree.get_leaves()
    assert len(leaves) == 2
    assert id2 in leaves
    assert id3 in leaves


def test_switch_branch(tmp_path):
    path = tmp_path / "session.jsonl"
    tree = SessionTree(path)
    id1 = tree.add_message(Message.user("hello"))
    id2 = tree.add_message(Message.assistant("hi"))

    tree.fork(id1)
    id3 = tree.add_message(Message.user("branch"))

    tree.switch_branch(id2)
    msgs = tree.get_messages()
    assert msgs[-1].content == "hi"

    tree.switch_branch(id3)
    msgs = tree.get_messages()
    assert msgs[-1].content == "branch"


def test_tool_calls_preserved(tmp_path):
    """Tool calls survive serialization roundtrip."""
    path = tmp_path / "session.jsonl"
    tree = SessionTree(path)
    tc = ToolCall(id="tc1", name="read_file", arguments={"path": "a.py"})
    tree.add_message(Message.assistant("reading", tool_calls=[tc]))

    tree2 = SessionTree(path)
    msgs = tree2.get_messages()
    assert len(msgs[0].tool_calls) == 1
    assert msgs[0].tool_calls[0].name == "read_file"


def test_call_id_preserved(tmp_path):
    """Tool result call_id survives roundtrip."""
    path = tmp_path / "session.jsonl"
    tree = SessionTree(path)
    tree.add_message(Message.tool("tc1", "file contents"))

    tree2 = SessionTree(path)
    msgs = tree2.get_messages()
    assert msgs[0].call_id == "tc1"


def test_compaction_entry(tmp_path):
    path = tmp_path / "session.jsonl"
    tree = SessionTree(path)
    tree.add_message(Message.user("hello"))
    tree.add_compaction(
        summary="Did stuff",
        first_kept_id="abc",
        tokens_before=5000,
        read_files=["a.py"],
        modified_files=["b.py"],
    )
    msgs = tree.get_messages()
    assert any("Did stuff" in m.content for m in msgs)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_session_tree.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write SessionTree implementation**

Create `chimera/sessions/tree.py` with the full implementation from the spec (already detailed there). Key points to ensure:

- `_serialize` must write `tool_calls` and `call_id`
- `_deserialize` must reconstruct `ToolCall` objects and `call_id`
- Use `Message.tool()` constructor for tool-role messages
- Corrupt JSONL lines are skipped (crash recovery)

The full implementation is in the spec document (`docs/superpowers/specs/2026-03-19-pi-mono-adoption-design.md`, Feature 4). Copy it verbatim, ensuring the serialization fixes from the spec review are included.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_session_tree.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add chimera/sessions/tree.py tests/test_session_tree.py
git commit -m "feat: session tree — JSONL persistence with branching"
```

---

### Task 9: Wire SessionTree into Session + CLI

**Files:**
- Modify: `chimera/sessions/session.py`
- Modify: `chimera/cli/code.py`

- [ ] **Step 1: Add tree parameter to Session**

In `chimera/sessions/session.py`, update `__init__`:

```python
    def __init__(
        self,
        agent: Agent,
        env: Environment | None = None,
        storage: Storage | None = None,
        session_id: SessionID | None = None,
        auto_compact: bool = False,
        compaction: CompactionStrategy | None = None,
        tree: SessionTree | None = None,
    ) -> None:
        # ... existing init ...
        self._tree = tree
```

Add `TYPE_CHECKING` import:
```python
    from chimera.sessions.tree import SessionTree
```

In `chat()`, add tree tracking:
```python
    def chat(self, message: str) -> AgentResult:
        self._context.add(Message.user(message))
        if self._tree:
            self._tree.add_message(Message.user(message))
        result = self._agent.loop.run(
            self._agent.provider, self._agent.tools, self._context, self._env,
        )
        if self._tree:
            self._tree.add_message(Message.assistant(result.output))
        return result
```

Add `switch_branch`:
```python
    def switch_branch(self, leaf_id: str) -> None:
        """Switch to a different branch and rebuild context."""
        if self._tree:
            self._tree.switch_branch(leaf_id)
            messages = self._tree.get_messages(leaf_id)
            self._context = Context(system=self._context.system)
            for msg in messages:
                self._context.add(msg)
```

- [ ] **Step 2: Add /tree, /branch, /switch commands to CLI**

In `chimera/cli/code.py`, add three new command handlers:

```python
def cmd_tree(session: Any, env: Any, args: str, out: PrintFn) -> None:
    tree = getattr(session, "_tree", None)
    if tree is None:
        out("No session tree active.")
        return
    leaves = tree.get_leaves()
    branch_points = tree.get_branch_points()
    out(f"Session tree: {tree.entry_count} entries, {len(leaves)} leaves, {len(branch_points)} branch points")
    out(f"Active leaf: {tree.active_leaf}")
    for leaf in leaves:
        marker = " <- active" if leaf == tree.active_leaf else ""
        branch = tree.get_branch(leaf)
        msg_count = sum(1 for e in branch if hasattr(e, 'message') and e.message)
        out(f"  {leaf[:8]}... ({msg_count} messages){marker}")


def cmd_branch(session: Any, env: Any, args: str, out: PrintFn) -> None:
    tree = getattr(session, "_tree", None)
    if tree is None:
        out("No session tree active.")
        return
    entry_id = args.strip()
    if not entry_id:
        out("Usage: /branch <entry_id>")
        return
    try:
        tree.fork(entry_id)
        out(f"Branched from {entry_id[:8]}...")
    except ValueError as e:
        out(str(e))


def cmd_switch(session: Any, env: Any, args: str, out: PrintFn) -> None:
    tree = getattr(session, "_tree", None)
    if tree is None:
        out("No session tree active.")
        return
    leaf_id = args.strip()
    if not leaf_id:
        out("Usage: /switch <leaf_id>")
        return
    try:
        session.switch_branch(leaf_id)
        out(f"Switched to branch {leaf_id[:8]}...")
    except ValueError as e:
        out(str(e))
```

Register them in `_COMMANDS`:
```python
    "tree": cmd_tree,
    "branch": cmd_branch,
    "switch": cmd_switch,
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_session_tree.py tests/test_session.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add chimera/sessions/session.py chimera/cli/code.py
git commit -m "feat: wire session tree into Session + CLI commands"
```

---

## Chunk 5: RPC Mode

### Task 10: RPC Types

**Files:**
- Create: `chimera/rpc/__init__.py`
- Create: `chimera/rpc/types.py`
- Create: `tests/test_rpc.py`

- [ ] **Step 1: Write failing tests for RPC types**

Create `tests/test_rpc.py`:

```python
"""Tests for chimera.rpc."""
import json
from dataclasses import asdict
from chimera.rpc.types import (
    PromptCommand, SteerCommand, CancelCommand, GetStateCommand,
    CompactCommand, SetModelCommand,
    RpcResponse, StateResponse,
    MessageEvent, TextDeltaEvent, ToolExecutionEvent, ErrorEvent,
)


def test_prompt_command_serializable():
    cmd = PromptCommand(message="hello", id="req-1")
    d = asdict(cmd)
    assert d["type"] == "prompt"
    assert d["message"] == "hello"
    assert json.dumps(d)  # Must be JSON-serializable


def test_rpc_response_serializable():
    resp = RpcResponse(command="prompt", id="req-1", success=True)
    d = asdict(resp)
    assert json.dumps(d)


def test_state_response():
    resp = StateResponse(
        id="req-2",
        messages=[{"role": "user", "content": "hi"}],
        model="glm-5",
        total_cost=0.05,
    )
    d = asdict(resp)
    assert d["model"] == "glm-5"


def test_message_event():
    evt = MessageEvent(role="assistant", content="hello", done=True)
    d = asdict(evt)
    assert d["type"] == "message"
    assert d["done"] is True


def test_tool_execution_event():
    evt = ToolExecutionEvent(
        tool_name="bash",
        arguments={"command": "ls"},
        phase="start",
    )
    d = asdict(evt)
    assert d["tool_name"] == "bash"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rpc.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create RPC package**

Create `chimera/rpc/__init__.py`:

```python
"""JSON-RPC mode for controlling Chimera agents via stdin/stdout."""
```

Create `chimera/rpc/types.py`:

```python
"""RPC command, response, and event types."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# --- Commands (client -> server) ---

@dataclass
class RpcCommand:
    """Base for all RPC commands."""
    type: str = ""
    id: str = ""


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
    command: str = ""
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
    type: str = ""


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
    phase: str = ""


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

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_rpc.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add chimera/rpc/__init__.py chimera/rpc/types.py tests/test_rpc.py
git commit -m "feat: RPC types — command/response/event dataclasses"
```

---

### Task 11: RPC Server + Handler

**Files:**
- Create: `chimera/rpc/server.py`
- Create: `chimera/rpc/handler.py`
- Modify: `tests/test_rpc.py`

- [ ] **Step 1: Write failing tests for server**

Add to `tests/test_rpc.py`:

```python
import io
from unittest.mock import MagicMock, patch
from chimera.rpc.server import RpcServer
from chimera.rpc.handler import RpcHandler


def test_server_parse_prompt_command():
    server = RpcServer.__new__(RpcServer)
    cmd = server._parse_command({"type": "prompt", "id": "1", "message": "hello"})
    assert isinstance(cmd, PromptCommand)
    assert cmd.message == "hello"


def test_server_parse_unknown_command():
    server = RpcServer.__new__(RpcServer)
    cmd = server._parse_command({"type": "unknown_xyz", "id": "1"})
    assert isinstance(cmd, RpcCommand)
    assert cmd.type == "unknown_xyz"


def test_server_emit(capsys):
    """Server should write JSON lines to stdout."""
    server = RpcServer.__new__(RpcServer)
    server._stdout = io.StringIO()
    server._emit(ErrorEvent(message="oops"))
    output = server._stdout.getvalue()
    parsed = json.loads(output.strip())
    assert parsed["type"] == "error"
    assert parsed["message"] == "oops"


def test_handler_get_state():
    """Handler should return session state."""
    mock_session = MagicMock()
    mock_session.messages = []
    mock_session._agent.provider.model_name = "glm-5"

    server = RpcServer.__new__(RpcServer)
    server._session = mock_session
    server._stdout = io.StringIO()

    handler = RpcHandler(server)
    handler.handle_get_state(GetStateCommand(id="req-1"))

    output = server._stdout.getvalue()
    parsed = json.loads(output.strip())
    assert parsed["command"] == "get_state"
    assert parsed["model"] == "glm-5"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rpc.py -v`
Expected: FAIL — `ModuleNotFoundError: chimera.rpc.server`

- [ ] **Step 3: Write RPC server**

Create `chimera/rpc/server.py`:

```python
"""stdin/stdout JSON-RPC server for headless agent control."""
from __future__ import annotations

import io
import json
import sys
from dataclasses import asdict
from typing import Any, TextIO

from chimera.rpc.types import (
    RpcCommand, RpcEvent, RpcResponse, ErrorEvent,
    PromptCommand, SteerCommand, CancelCommand,
    GetStateCommand, CompactCommand, SetModelCommand,
)


_COMMAND_MAP = {
    "prompt": PromptCommand,
    "steer": SteerCommand,
    "cancel": CancelCommand,
    "get_state": GetStateCommand,
    "compact": CompactCommand,
    "set_model": SetModelCommand,
}


class RpcServer:
    """JSON-line RPC server over stdin/stdout."""

    def __init__(self, session: Any, stdin: TextIO | None = None,
                 stdout: TextIO | None = None) -> None:
        self._session = session
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout
        self._handlers: dict[str, Any] = {}

    def set_handlers(self, handlers: dict[str, Any]) -> None:
        """Set command handlers."""
        self._handlers = handlers

    def run(self) -> None:
        """Main loop: read commands, dispatch, emit responses."""
        for line in self._stdin:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                command = self._parse_command(raw)
                self._dispatch(command)
            except json.JSONDecodeError as e:
                self._emit(ErrorEvent(message=f"Invalid JSON: {e}"))
            except Exception as e:
                self._emit(ErrorEvent(message=str(e)))

    def _parse_command(self, raw: dict[str, Any]) -> RpcCommand:
        """Parse a raw dict into a typed command."""
        cmd_type = raw.get("type", "")
        cls = _COMMAND_MAP.get(cmd_type, RpcCommand)
        # Build from matching fields
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore
        filtered = {k: v for k, v in raw.items() if k in valid_fields}
        return cls(**filtered)

    def _dispatch(self, command: RpcCommand) -> None:
        """Route command to handler."""
        handler = self._handlers.get(command.type)
        if handler is None:
            self._emit(RpcResponse(
                command=command.type, id=command.id,
                success=False, error=f"Unknown command: {command.type}",
            ))
            return
        handler(command)

    def _emit(self, event_or_response: RpcEvent | RpcResponse) -> None:
        """Write JSON line to stdout."""
        self._stdout.write(json.dumps(asdict(event_or_response)) + "\n")
        self._stdout.flush()
```

- [ ] **Step 4: Write RPC handler**

Create `chimera/rpc/handler.py`:

```python
"""Command handlers for RPC server."""
from __future__ import annotations

from typing import Any, Callable

from chimera.rpc.types import (
    PromptCommand, SteerCommand, CancelCommand,
    GetStateCommand, CompactCommand,
    RpcResponse, StateResponse,
    MessageEvent, ToolExecutionEvent, ErrorEvent,
)


class RpcHandler:
    """Maps RPC commands to agent/session operations."""

    def __init__(self, server: Any) -> None:
        self._server = server

    def handle_prompt(self, cmd: PromptCommand) -> None:
        """Run a prompt through the session."""
        from chimera.core.loop import drain_steps

        session = self._server._session
        try:
            gen = session.iter_chat(cmd.message)
            for step in gen:
                if step.tool_calls:
                    for i, tc in enumerate(step.tool_calls):
                        self._server._emit(ToolExecutionEvent(
                            tool_name=tc.name, arguments=tc.arguments, phase="start",
                        ))
                        if step.tool_results and i < len(step.tool_results):
                            tr = step.tool_results[i]
                            self._server._emit(ToolExecutionEvent(
                                tool_name=tc.name,
                                result=tr.output[:2000],
                                success=tr.success, phase="end",
                            ))
                if step.done and step.message:
                    self._server._emit(MessageEvent(
                        role="assistant", content=step.message.content, done=True,
                    ))
                if step.pending_approval:
                    step.pending_approval.deny("RPC mode: auto-denied")
        except Exception as e:
            self._server._emit(ErrorEvent(message=str(e)))
            return
        self._server._emit(RpcResponse(command="prompt", id=cmd.id))

    def handle_steer(self, cmd: SteerCommand) -> None:
        self._server._session.steer(cmd.message)
        self._server._emit(RpcResponse(command="steer", id=cmd.id))

    def handle_cancel(self, cmd: CancelCommand) -> None:
        if hasattr(self._server._session, "cancel"):
            self._server._session.cancel()
        self._server._emit(RpcResponse(command="cancel", id=cmd.id))

    def handle_get_state(self, cmd: GetStateCommand) -> None:
        session = self._server._session
        self._server._emit(StateResponse(
            id=cmd.id,
            messages=[{"role": m.role, "content": m.content}
                      for m in session.messages],
            model=session._agent.provider.model_name,
        ))

    def handle_compact(self, cmd: CompactCommand) -> None:
        if hasattr(self._server._session, "compact"):
            self._server._session.compact()
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

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_rpc.py -v`
Expected: All pass

- [ ] **Step 6: Add `--mode` flag to CLI**

In `chimera/cli/code.py`, at the top of `run_code()`, add mode handling:

```python
def run_code(args: Any) -> int:
    mode = getattr(args, "mode", "interactive")

    if mode == "rpc":
        return _run_rpc_mode(args)
    elif mode == "json":
        return _run_json_mode(args)

    # ... existing interactive REPL code ...
```

Add the RPC mode function:

```python
def _run_rpc_mode(args: Any) -> int:
    """Run in headless RPC mode (stdin/stdout JSON lines)."""
    workdir = os.path.abspath(getattr(args, "workdir", None) or os.getcwd())
    provider = create_provider(model=getattr(args, "model", None))
    env = LocalEnvironment(workdir=workdir)
    env.setup()

    config = LoopConfig()
    loop = ReAct(max_steps=getattr(args, "max_steps", 50) or 50, config=config)
    prompt = Prompt.from_string(_DEFAULT_SYSTEM)
    tools = list(AGENT_TOOLS)
    agent = Agent(provider=provider, tools=tools, loop=loop, prompt=prompt)
    session = Session(agent=agent, env=env)

    from chimera.rpc.server import RpcServer
    from chimera.rpc.handler import RpcHandler

    server = RpcServer(session)
    handler = RpcHandler(server)
    server.set_handlers(handler.handlers)
    server.run()

    env.cleanup()
    return 0


def _run_json_mode(args: Any) -> int:
    """Run in JSON output mode (events only, no interactive input)."""
    # Placeholder — reads single prompt from stdin, outputs events
    return 0
```

In `chimera/cli/main.py`, add `--mode` argument to the `code` subparser (find the `code` subparser setup and add):

```python
    code_parser.add_argument("--mode", choices=["interactive", "rpc", "json"], default="interactive")
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_rpc.py -v`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add chimera/rpc/server.py chimera/rpc/handler.py chimera/cli/code.py tests/test_rpc.py
git commit -m "feat: RPC mode — headless JSON-RPC agent control via stdin/stdout"
```

---

## Chunk 6: Final Integration + Full Test Suite

### Task 12: Full Integration Test

**Files:**
- Modify: `tests/test_rpc.py` (or create `tests/test_pi_mono_integration.py`)

- [ ] **Step 1: Write integration test**

Create `tests/test_pi_mono_integration.py`:

```python
"""Integration test: all 7 pi-mono features wired together."""
from chimera.core.agent import Agent
from chimera.core.cancellation import CancellationToken
from chimera.core.file_tracker import FileTracker
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.core.message_queue import MessageQueues
from chimera.core.operations import LocalReadOps, LocalBashOps
from chimera.core.prompt import Prompt
from chimera.providers.registry import list_providers, _ensure_builtins_registered
from chimera.sessions.session import Session
from chimera.sessions.tree import SessionTree
from chimera.tools.read import ReadFileTool
from chimera.tools.bash import BashTool


def test_all_features_wire_together():
    """Verify all 7 features can be instantiated and wired into an Agent."""
    _ensure_builtins_registered()
    assert "anthropic" in list_providers()

    queues = MessageQueues()
    tracker = FileTracker()
    cancel = CancellationToken()
    config = LoopConfig(
        message_queues=queues,
        file_tracker=tracker,
        cancellation=cancel,
    )
    loop = ReAct(max_steps=5, config=config)

    read_ops = LocalReadOps(cwd="/tmp")
    bash_ops = LocalBashOps(cwd="/tmp")
    tools = [
        ReadFileTool(ops=read_ops),
        BashTool(ops=bash_ops),
    ]

    from chimera.providers.base import Provider, Response
    class MockProvider(Provider):
        def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
            return Response(content="done", tool_calls=[], usage={"input_tokens": 10, "output_tokens": 5})
        @property
        def context_window(self): return 1000
        @property
        def supports_tool_use(self): return True
        @property
        def model_name(self): return "mock"

    agent = Agent(
        provider=MockProvider(),
        tools=tools,
        loop=loop,
        prompt=Prompt.from_string("test"),
    )
    result = agent.run("test", env=None)
    assert result.success


def test_session_tree_with_session(tmp_path):
    """SessionTree wired into Session."""
    tree = SessionTree(tmp_path / "session.jsonl")

    from chimera.providers.base import Provider, Response
    class MockProvider(Provider):
        def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
            return Response(content="hi", tool_calls=[], usage={"input_tokens": 5, "output_tokens": 3})
        @property
        def context_window(self): return 1000
        @property
        def supports_tool_use(self): return True
        @property
        def model_name(self): return "mock"

    agent = Agent(provider=MockProvider(), loop=ReAct(max_steps=5))
    session = Session(agent=agent, tree=tree)
    result = session.chat("hello")
    assert result.output == "hi"
    assert tree.entry_count >= 2  # user + assistant
```

- [ ] **Step 2: Run integration test**

Run: `uv run pytest tests/test_pi_mono_integration.py -v`
Expected: All pass

- [ ] **Step 3: Run FULL test suite**

Run: `uv run pytest --timeout=120`
Expected: All 2301+ tests pass, new tests add ~50 more

- [ ] **Step 4: Commit**

```bash
git add tests/test_pi_mono_integration.py
git commit -m "test: integration test for all 7 pi-mono features"
```

---

### Task 13: Final — Update CLI main.py + Exports

- [ ] **Step 1: Ensure chimera/__init__.py exports new modules**

Check that `chimera/__init__.py` re-exports the key new classes. Add if missing:

```python
from chimera.core.file_tracker import FileTracker
from chimera.core.message_queue import MessageQueues
from chimera.core.cancellation import CancellationToken, OperationCancelled
from chimera.core.operations import ReadOps, WriteOps, BashOps, SearchOps
from chimera.sessions.tree import SessionTree
from chimera.providers.registry import register_provider, list_providers
```

- [ ] **Step 2: Run final full suite**

Run: `uv run pytest --timeout=120`
Expected: All pass

- [ ] **Step 3: Run linter and type checker**

Run: `uv run ruff check chimera/`
Run: `uv run mypy chimera/`
Fix any issues.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete pi-mono pattern adoption — 7 features"
```
