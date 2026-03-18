# Retry Loop

`chimera.core.loops.retry` wraps any inner loop with retry + scoring
semantics. After each attempt the result is scored; if the score meets
a threshold the loop stops early, otherwise a fresh attempt is started
with feedback from the previous failure. The best attempt (by score)
is returned.

Inspired by SWE-Agent's `AbstractRetryLoop` / `ScoreRetryLoop`.

## Key Classes

| Class | Description |
|-------|-------------|
| `RetryLoop` | Retry wrapper -- runs an inner loop up to N times, selects the best attempt |
| `RetryAttempt` | Record of a single attempt with `attempt`, `result`, and `score` fields |

## Quick Start

```python
from chimera.core.loop import ReAct
from chimera.core.loops.retry import RetryLoop

loop = RetryLoop(
    inner=ReAct(max_steps=20),
    max_retries=3,
    success_threshold=1.0,
)

result = loop.run(provider, tools, context, env)
print(f"Best score: {max(a.score for a in loop.attempts)}")
```

## Custom Scorer

By default, the scorer returns 1.0 for success and 0.0 otherwise.
Pass a custom scorer for finer-grained evaluation:

```python
def test_score(result):
    """Score based on how many tests pass."""
    if "5 passed" in result.output:
        return 1.0
    elif "3 passed" in result.output:
        return 0.6
    return 0.0

loop = RetryLoop(inner=ReAct(), scorer=test_score, max_retries=5)
```

## Retry Context

On each retry, `RetryLoop` builds a fresh context containing the
original user message plus feedback from the previous failure. This
gives the inner loop a chance to try a different approach without
being influenced by the previous conversation history.

## Integration with LoopConfig

Pass a `LoopConfig` to forward permissions, events, and detection
to the inner loop:

```python
config = chimera.LoopConfig(permissions=policy, events=bus)
loop = RetryLoop(inner=ReAct(max_steps=20, config=config))
```

## Import Reference

```python
from chimera.core.loops.retry import RetryLoop, RetryAttempt
```

## Related

- [Loops](../concepts/loops.md) -- overview of all loop types
- [Plan/Act Loop](plan-act-loop.md) -- two-phase plan then execute
- [Lint Feedback Loop](lint-feedback-loop.md) -- linter-driven iteration
