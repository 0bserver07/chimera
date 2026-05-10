---
title: "File Tracker"
description: "File Tracker"
---

`chimera.core.file_tracker` tracks which files the agent has read or modified
during a session so that the information survives context compaction.

## FileTracker

| Method | Description |
|--------|-------------|
| `record_read(path)` | Record a file as read (deduplicated) |
| `record_modified(path)` | Record a file as modified (deduplicated) |
| `to_metadata()` | Return a `CompactionMetadata` snapshot |
| `to_prompt_section()` | Return a markdown section listing all touched files |

Paths are recorded in insertion order and deduplicated internally, so calling
`record_read` twice with the same path has no effect.

## CompactionMetadata

Defined in `chimera.compaction.base`, `CompactionMetadata` carries file lists
across compaction boundaries:

| Method | Description |
|--------|-------------|
| `merge(other)` | Combine two metadata objects (deduplicates, keeps `other` token counts) |
| `to_prompt_section()` | Render a `## Files you've been working with` section |

Fields: `read_files`, `modified_files`, `tokens_before`, `tokens_after`.

## FileAwareCompaction mixin

`FileAwareCompaction` extends `CompactionStrategy` so that strategies can
inject file context into their summaries:

| Method | Description |
|--------|-------------|
| `set_metadata(metadata)` | Store a `CompactionMetadata` instance |
| `get_file_prompt_section()` | Retrieve the formatted prompt section (empty string if none set) |

`SummaryCompaction` uses this mixin: before asking the model to summarise the
conversation it prepends `get_file_prompt_section()` so the summary always
mentions which files were touched.

## Example

```python
from chimera.core.file_tracker import FileTracker

tracker = FileTracker()
tracker.record_read("src/auth.py")
tracker.record_modified("src/auth.py")
tracker.record_read("tests/test_auth.py")

print(tracker.to_prompt_section())
# ## Files you've been working with
# Modified: src/auth.py
# Read: src/auth.py, tests/test_auth.py

meta = tracker.to_metadata()
# Pass to compaction strategy
strategy.set_metadata(meta)
```

## LoopConfig wiring

Pass a tracker through `LoopConfig.file_tracker` so reads / writes
performed by tools land in the same shared instance:

```python
from chimera.core.file_tracker import FileTracker
from chimera.core.loop_config import LoopConfig

tracker = FileTracker()
config = LoopConfig(file_tracker=tracker)
# ...run the loop, then introspect:
print(tracker.to_prompt_section())
```

The shared `tool_executor` records every successful `read_file` /
`write_file` / `edit_file` / `replace_in_file` / `apply_patch` invocation
on the configured tracker so subsequent compaction passes can render a
"Files you've been working with" section into the rolling summary.

## Related

- [LoopConfig](/modules/loop-config/) — `file_tracker` field
- [Compaction](/modules/compaction/) — `FileAwareCompaction` mixin
- [Checkpoints](/modules/checkpoints/) — `GhostCommitManager` for file-undo
