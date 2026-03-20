---
title: "Session Tree"
description: "Session Tree"
---

`chimera.sessions.tree` persists conversation history as a JSONL file where
entries form a tree via `parent_id`.  Branching is append-only — no existing
data is rewritten.

## Entry types

| Class | `type` field | Key fields |
|-------|-------------|------------|
| `SessionHeader` | `header` | `version`, `cwd`, `system_prompt` |
| `MessageEntry` | `message` | `message` (`Message` object) |
| `CompactionEntry` | `compaction` | `summary`, `first_kept_entry_id`, `tokens_before`, `read_files`, `modified_files` |
| `LabelEntry` | `label` | `target_id`, `label` |
| `SessionEntry` | *(custom)* | `data` dict for extension types |

Every entry carries `id` (12-char hex UUID), `parent_id`, and `timestamp`.

## SessionTree

| Method / Property | Description |
|-------------------|-------------|
| `add_message(message, parent_id)` | Append a `MessageEntry`; returns the new entry ID |
| `add_compaction(summary, first_kept_id, tokens_before, ...)` | Append a `CompactionEntry` |
| `add_label(target_id, label)` | Tag an existing entry with a human-readable label |
| `append(entry)` | Low-level: append any `AnyEntry` and flush to JSONL |
| `get_branch(leaf_id)` | Walk parent pointers from `leaf_id` to root; returns ordered list |
| `get_messages(leaf_id)` | Extract `Message` objects from the branch (compactions become summary messages) |
| `fork(from_entry_id)` | Set `_active_leaf` to a historical entry, enabling a new branch |
| `switch_branch(leaf_id)` | Activate an existing leaf |
| `get_leaves()` | Return IDs of all entries with no children |
| `get_branch_points()` | Return entry IDs that have more than one child |
| `active_leaf` | The current leaf ID |
| `entry_count` | Total number of entries in the file |

The constructor loads an existing JSONL file if the path exists.  All writes
use append mode, making them safe for concurrent readers.

## Example — branching

```python
from chimera.sessions.tree import SessionTree
from chimera.types import Message

tree = SessionTree("/tmp/session.jsonl")

id1 = tree.add_message(Message.user("Hello"))
id2 = tree.add_message(Message.assistant("Hi there"))

# Create a branch from the first message
tree.fork(id1)
id3 = tree.add_message(Message.user("Actually, start over"))

# Two leaves now exist
print(tree.get_leaves())          # [id2, id3]
print(tree.get_branch_points())   # [id1]

# Read the alternate branch
msgs = tree.get_messages(id3)
```
