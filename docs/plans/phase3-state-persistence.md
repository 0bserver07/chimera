# Phase 3: State & Persistence — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add content replacement state machine for prompt cache stability, file state cache for read deduplication, JSONL session transcripts with sidechain pattern for sub-agents, and session resume with state reconstruction.

**Architecture:** `ContentReplacementState` tracks persistence decisions (frozen once made). `FileStateCache` LRU deduplicates reads. `TranscriptStorage` writes incremental JSONL with sidechains. `SessionResumer` reconstructs state on resume.

**Tech Stack:** Python 3.11+, asyncio, aiofiles, dataclasses

**Spec:** `research/specs/phase3-state-persistence.md`

**Depends on:** Phase 1 (LoopEvent, LoopState)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `chimera/core/content_replacement.py` | CREATE | `ContentReplacementState`, `ContentReplacementEntry` |
| `chimera/core/tool_result_persister.py` | CREATE | `ToolResultPersister` |
| `chimera/core/file_state_cache.py` | CREATE | `FileStateCache`, `FileStateEntry` |
| `chimera/core/uuid_chain.py` | CREATE | `UUIDChain` |
| `chimera/sessions/transcript.py` | CREATE | `TranscriptStorage` |
| `chimera/sessions/resume.py` | CREATE | `SessionResumer` |
| `chimera/tools/read.py` | MODIFY | Integrate `FileStateCache` |
| `tests/core/test_content_replacement.py` | CREATE | |
| `tests/core/test_file_state_cache.py` | CREATE | |
| `tests/core/test_uuid_chain.py` | CREATE | |
| `tests/sessions/test_transcript.py` | CREATE | |
| `tests/sessions/test_resume.py` | CREATE | |

---

### Task 1: ContentReplacementState

- [ ] **Step 1: Write tests**

```python
# tests/core/test_content_replacement.py
from chimera.core.content_replacement import ContentReplacementState, ContentReplacementEntry

def test_should_persist_above_threshold():
    state = ContentReplacementState(per_tool_max_chars=100)
    assert state.should_persist("t1", 200) is True
    assert state.should_persist("t2", 50) is False

def test_decision_is_frozen():
    state = ContentReplacementState(per_tool_max_chars=100)
    state.record_decision("t1", persisted_path="/tmp/t1.json", preview="preview", original_size=500)
    # Same ID returns same answer even if size changed
    assert state.should_persist("t1", 10) is True  # Frozen as persisted

def test_inline_decision_frozen():
    state = ContentReplacementState(per_tool_max_chars=100)
    state.record_decision("t2")  # No persisted_path = inline
    assert state.should_persist("t2", 99999) is False  # Frozen as inline

def test_clone():
    state = ContentReplacementState()
    state.record_decision("t1", persisted_path="/tmp/t1.json", preview="p", original_size=100)
    cloned = state.clone()
    assert "t1" in cloned.seen_ids
    # Mutations to clone don't affect original
    cloned.record_decision("t2")
    assert "t2" not in state.seen_ids

def test_reconstruct_from_entries():
    entries = [ContentReplacementEntry(tool_use_id="t1", persisted_path="/tmp/t1.json", preview="p", original_size=100, timestamp=0)]
    state = ContentReplacementState.reconstruct_from_transcript(entries)
    assert "t1" in state.seen_ids
    assert state.should_persist("t1", 1) is True
```

- [ ] **Step 2: Run test, verify fail**
- [ ] **Step 3: Implement content_replacement.py** (follow spec Section 1)
- [ ] **Step 4: Run test, verify pass**
- [ ] **Step 5: Commit**

---

### Task 2: ToolResultPersister

- [ ] **Step 1: Write tests**

```python
# tests/core/test_tool_result_persister.py
import pytest
import tempfile
from pathlib import Path
from chimera.core.tool_result_persister import ToolResultPersister

@pytest.mark.asyncio
async def test_persist_and_read():
    with tempfile.TemporaryDirectory() as tmpdir:
        persister = ToolResultPersister(Path(tmpdir))
        path, preview = await persister.persist("t1", "x" * 10000)
        assert Path(path).exists()
        assert len(preview) <= persister.preview_size + 100  # Allow for truncation marker
        content = await persister.read("t1")
        assert content == "x" * 10000

@pytest.mark.asyncio
async def test_read_nonexistent():
    with tempfile.TemporaryDirectory() as tmpdir:
        persister = ToolResultPersister(Path(tmpdir))
        assert await persister.read("nope") is None
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 3: FileStateCache

- [ ] **Step 1: Write tests**

```python
# tests/core/test_file_state_cache.py
import tempfile, os
from pathlib import Path
from chimera.core.file_state_cache import FileStateCache

def test_cache_hit():
    cache = FileStateCache()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("hello")
        f.flush()
        mtime = os.path.getmtime(f.name)
        cache.put(f.name, "hello", mtime, None, None)
        entry = cache.get(f.name, None, None)
        assert entry is not None
        assert entry.content == "hello"
        os.unlink(f.name)

def test_cache_miss_after_modification():
    cache = FileStateCache()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("v1")
        f.flush()
        mtime = os.path.getmtime(f.name)
        cache.put(f.name, "v1", mtime, None, None)
        # Modify file
        Path(f.name).write_text("v2")
        entry = cache.get(f.name, None, None)
        assert entry is None
        os.unlink(f.name)

def test_clone_is_independent():
    cache = FileStateCache()
    cache.put("/tmp/test", "data", 1.0, None, None)
    cloned = cache.clone()
    cloned.put("/tmp/other", "other", 2.0, None, None)
    assert cache.get("/tmp/other", None, None) is None

def test_lru_eviction():
    cache = FileStateCache(max_entries=2)
    cache.put("/a", "a", 1.0, None, None)
    cache.put("/b", "b", 2.0, None, None)
    cache.put("/c", "c", 3.0, None, None)
    assert cache.get("/a", None, None) is None  # Evicted
    assert cache.get("/b", None, None) is not None
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 4: UUIDChain

- [ ] **Step 1: Write tests**

```python
# tests/core/test_uuid_chain.py
from chimera.core.uuid_chain import UUIDChain
from chimera.types import Message

def test_first_message_has_no_parent():
    chain = UUIDChain()
    parent = chain.next(Message.user("hello"))
    assert parent is None

def test_second_message_has_parent():
    chain = UUIDChain()
    chain.next(Message.user("hello"))
    parent = chain.next(Message.assistant("hi"))
    assert parent is not None
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 5: TranscriptStorage

- [ ] **Step 1: Write tests**

```python
# tests/sessions/test_transcript.py
import pytest
import tempfile
from pathlib import Path
from chimera.sessions.transcript import TranscriptStorage
from chimera.types import Message

@pytest.mark.asyncio
async def test_record_and_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = TranscriptStorage(Path(tmpdir), "session1")
        await storage.record(Message.user("hello"), parent_uuid=None)
        await storage.record(Message.assistant("hi"), parent_uuid="uuid1")
        messages = await storage.load()
        assert len(messages) == 2

@pytest.mark.asyncio
async def test_subagent_sidechain():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = TranscriptStorage(Path(tmpdir), "session1")
        await storage.record_subagent("agent1", Message.user("sub task"), parent_uuid=None)
        messages = await storage.load_subagent("agent1")
        assert len(messages) == 1

@pytest.mark.asyncio
async def test_progress_messages_not_persisted():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = TranscriptStorage(Path(tmpdir), "session1")
        msg = Message.assistant("progress")
        msg.metadata = {"type": "progress"}  # Will need Message to support this
        await storage.record(msg)
        messages = await storage.load()
        # Progress should be filtered
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 6: SessionResumer

- [ ] **Step 1: Write tests**

```python
# tests/sessions/test_resume.py
import pytest
import tempfile
from pathlib import Path
from chimera.sessions.resume import SessionResumer
from chimera.sessions.transcript import TranscriptStorage
from chimera.core.content_replacement import ContentReplacementState, ContentReplacementEntry

@pytest.mark.asyncio
async def test_resume_reconstructs_content_replacement_state():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = TranscriptStorage(Path(tmpdir), "session1")
        # Record a message with content replacement metadata
        msg = Message.user("test")
        msg.metadata = {"content_replacement_entry": {
            "tool_use_id": "t1", "persisted_path": "/tmp/t1.json",
            "preview": "preview", "original_size": 5000, "timestamp": 0,
        }}
        await storage.record(msg)
        resumer = SessionResumer()
        messages, cr_state = await resumer.resume("session1", storage, None)
        assert "t1" in cr_state.seen_ids
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 7: Integrate FileStateCache into ReadFileTool

- [ ] **Step 1: Write test for deduplication**
- [ ] **Step 2: Modify `chimera/tools/read.py` to accept and use `FileStateCache`**
- [ ] **Step 3: Run existing tests to verify no breakage**
- [ ] **Step 4: Commit**

---

### Task 8: Integration — Full Persistence Flow

- [ ] **Step 1: Write integration test: agent runs, messages are recorded, session resumes**
- [ ] **Step 2: Run test, verify pass**
- [ ] **Step 3: Run full test suite**
- [ ] **Step 4: Commit**
