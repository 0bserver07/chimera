---
title: "Long-Term Memory"
description: "Long-Term Memory"
---

`chimera.sessions.long_term_memory` provides persistent memory that survives
across agent sessions. Store facts, learnings, preferences, and project
context that the agent can recall later. Backed by a JSON file for simplicity.

Inspired by Codex's memory consolidation system.

## Key Classes

| Class | Description |
|-------|-------------|
| `LongTermMemory` | Key-value memory store with categories, search, and prompt rendering |
| `MemoryEntry` | A single entry with `key`, `content`, `category`, timestamps, and `metadata` |

## Quick Start

```python
from chimera.sessions.long_term_memory import LongTermMemory

memory = LongTermMemory("~/.chimera/memory.json")

memory.store("user_name", "Alice", category="preference")
memory.store("project_lang", "Python 3.12", category="project")
memory.store("test_framework", "pytest", category="project")

# In a later session:
memory = LongTermMemory("~/.chimera/memory.json")
name = memory.recall("user_name")  # "Alice"
```

## Categories and Search

```python
# Recall all memories in a category
project = memory.recall_category("project")

# Search by substring (case-insensitive, matches key and content)
results = memory.search("python")
for entry in results:
    print(f"{entry.key}: {entry.content}")
```

## Prompt Injection

`to_prompt_section()` renders memories as Markdown for injection into
system prompts:

```python
section = memory.to_prompt_section(categories=["preference", "project"])
# Returns:
# ## Agent Memory
# ### Preference
# - **user_name**: Alice
# ### Project
# - **project_lang**: Python 3.12
```

## Managing Memories

```python
memory.forget("old_key")   # Remove a single entry (returns True/False)
memory.clear()              # Remove all entries
print(memory.count)         # Number of stored memories
```

## Import Reference

```python
from chimera.sessions.long_term_memory import LongTermMemory, MemoryEntry
```

## Related

- [Sessions](sessions.md) -- multi-turn conversation persistence
- [Checkpoints](checkpoints.md) -- named context snapshots
