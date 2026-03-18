# History Processor

`chimera.context.history` provides composable processors for transforming
conversation history before sending it to the LLM.

## Key Classes

| Class | Description |
|-------|-------------|
| `HistoryProcessor` | Abstract base class -- implement `process(messages)` |
| `TruncateProcessor` | Keep only the last N messages |
| `PruneProcessor` | Replace old tool result content with `[pruned]` |
| `CompressProcessor` | Compress old messages into a summary, keep recent ones intact |
| `CompositeProcessor` | Chain multiple processors in sequence |

## Quick Start

```python
from chimera.context.history import TruncateProcessor, PruneProcessor, CompositeProcessor

# Keep last 20 messages, prune old tool results beyond the 3 most recent
processor = CompositeProcessor([
    PruneProcessor(keep_last_n_results=3),
    TruncateProcessor(max_messages=20),
])

cleaned = processor.process(messages)
```

## Processors

### TruncateProcessor

Keeps only the last N messages, discarding everything older.

```python
proc = TruncateProcessor(max_messages=15)
```

### PruneProcessor

Replaces old tool result content with `[pruned]`, preserving structure.

```python
proc = PruneProcessor(keep_last_n_results=5)
```

### CompressProcessor

Compresses old messages into a summary, keeping recent ones intact.
Uses simple concatenation and truncation (no LLM call).

```python
proc = CompressProcessor(keep_recent=5, max_summary_tokens=500)
```

### CompositeProcessor

Chains multiple processors -- each processor's output becomes the next input.

```python
proc = CompositeProcessor([PruneProcessor(), TruncateProcessor()])
```

## Custom Processors

Subclass `HistoryProcessor` and implement `process()`:

```python
from chimera.context.history import HistoryProcessor

class DropSystemProcessor(HistoryProcessor):
    def process(self, messages):
        return [m for m in messages if m.role != "system"]
```

## Related

- [Compaction](compaction.md) -- threshold-based context compaction
- [Focus Chain](focus-chain.md) -- token-budget context selection
