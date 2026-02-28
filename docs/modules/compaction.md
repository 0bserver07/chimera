# Compaction

`chimera.compaction` manages context window size by reducing message lists so
they fit within a token budget.  Three strategies can be used individually or
chained together in a composite pipeline.

## CompactionStrategy (ABC)

Every strategy implements a single method:

```python
class CompactionStrategy(ABC):
    @abstractmethod
    def compact(self, messages: list[Message], budget: int) -> list[Message]:
        """Return a compacted copy that fits within *budget* tokens."""
```

Implementations must not mutate the original list or its elements.

## TokenCounter

Estimates token counts for text and message lists.

- When `tiktoken` is installed, uses the given encoding model (default
  `cl100k_base`) for precise counts.
- Otherwise falls back to a `len(text) // 4` character-based heuristic.

The counter exposes two methods:

| Method | Description |
|--------|-------------|
| `count(text)` | Token count for a single string |
| `count_messages(messages)` | Sum of tokens across all message content and serialised tool-call arguments |

## Built-in strategies

### PruneCompaction

Truncates oversized tool-result messages.  For every `tool` message exceeding
`max_tool_output_lines` (default 50), the middle is replaced with
`... [truncated] ...` while preserving the first 20 and last 20 lines.

```python
from chimera.compaction import PruneCompaction

pruner = PruneCompaction(max_tool_output_lines=80)
compacted = pruner.compact(messages, budget=8000)
```

### SummaryCompaction

Replaces the middle portion of a conversation with a summary.  The first
`keep_first` (default 2) and last `keep_last` (default 10) messages are
preserved; everything in between is summarised.

- With a `Provider` -- uses an LLM call to produce a concise summary paragraph.
- Without a provider -- produces a simple count of messages by role.

```python
from chimera.compaction import SummaryCompaction

# Text-only fallback
summary = SummaryCompaction(keep_first=2, keep_last=10)

# LLM-powered summary
summary_llm = SummaryCompaction(
    provider=my_provider,
    keep_first=2,
    keep_last=10,
    summary_max_tokens=500,
)
```

### CompositeCompaction

Chains multiple strategies sequentially.  After each strategy the token count
is re-evaluated and the pipeline **short-circuits** as soon as the result fits
within the budget.

```python
from chimera.compaction import CompositeCompaction, PruneCompaction, SummaryCompaction

pipeline = CompositeCompaction([
    PruneCompaction(max_tool_output_lines=50),
    SummaryCompaction(keep_first=2, keep_last=10),
])

compacted = pipeline.compact(messages, budget=8000)
```

## Compaction pipeline

The following diagram shows how `CompositeCompaction` processes messages
through multiple stages:

```mermaid
flowchart LR
    IN[Messages] --> CHK1{Under budget?}
    CHK1 -- Yes --> OUT[Return]
    CHK1 -- No --> P[PruneCompaction]
    P --> CHK2{Under budget?}
    CHK2 -- Yes --> OUT
    CHK2 -- No --> S[SummaryCompaction]
    S --> OUT
```

## Integration with Sessions

When `auto_compact=True` is set on a `Session`, the compaction strategy runs
after every `chat` turn:

```python
from chimera.sessions import Session
from chimera.compaction import CompositeCompaction, PruneCompaction, SummaryCompaction

pipeline = CompositeCompaction([
    PruneCompaction(),
    SummaryCompaction(provider=my_provider),
])

session = Session(
    agent=agent,
    auto_compact=True,
    compaction=pipeline,
)
```
