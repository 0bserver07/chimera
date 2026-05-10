---
title: "chimera.compaction"
description: "Reference for chimera.compaction — strategies that rewrite conversation history when the context window fills."
---

`chimera.compaction` rewrites old messages so a long-running session
keeps fitting in the context window.

## Top-level exports

```python
from chimera.compaction import (
    CompactionStrategy,
    SummaryCompaction,
    PruneCompaction,
    CounterCompaction,
    CompositeCompaction,
    ThresholdCompaction,
    AtomicGroup,
    CompactionView,
    CompactionUrgency,
    CompactionMetadata,
    FileAwareCompaction,
)
```

| Symbol | Module | Purpose |
|---|---|---|
| `CompactionStrategy` | `chimera.compaction.base` | ABC. Override `compact(view, urgency) -> NewMessages`. |
| `AtomicGroup` | `chimera.compaction.base` | Tool call + result pair that must compact together. |
| `CompactionView` | `chimera.compaction.base` | Read-only window over the message history. |
| `CompactionUrgency` | `chimera.compaction.base` | Enum: `SOFT`, `HARD`. Drives strategy choice. |
| `CompactionMetadata` | `chimera.compaction.base` | What was kept, what was summarised, file references preserved. |
| `FileAwareCompaction` | `chimera.compaction.base` | Mixin that pulls `FileTracker` references into summaries. |
| `SummaryCompaction` | `chimera.compaction.summary` | LLM-summarises old turns; subclass of `FileAwareCompaction`. |
| `PruneCompaction` | `chimera.compaction.strategies` | Drop oldest N turns. |
| `CounterCompaction` | `chimera.compaction.strategies` | Drop after a fixed message count. |
| `CompositeCompaction` | `chimera.compaction.strategies` | Run a list of strategies in order. |
| `ThresholdCompaction` | `chimera.compaction.thresholds` | Trigger SOFT at one token budget, HARD at another, with tool-call/result atomicity. |

Wire into a loop via `LoopConfig(compaction=...)`.

## See also

- [`chimera.core`](/reference/core/) for `LoopConfig`.
- [`chimera.events`](/reference/events/) for `CompactionEvent`.
