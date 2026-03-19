---
title: "Detection"
description: "Detection"
---

`chimera.detection` identifies when an agent falls into a repetitive loop by
analysing tool-call history.  Two strategies detect different repetition
patterns and can be combined via a composite detector.

## Base types

### DetectionResult

A frozen dataclass returned when a strategy fires:

| Field | Type | Description |
|-------|------|-------------|
| `detected` | `bool` | Whether a loop was found |
| `strategy` | `str` | Name of the triggering strategy (e.g. `"exact_repeat"`) |
| `pattern` | `str` | Human-readable description of the detected pattern |
| `confidence` | `float` | Value in `[0, 1]` expressing certainty (default `1.0`) |

### DetectionStrategy (ABC)

Every detector implements three methods:

| Method | Description |
|--------|-------------|
| `record(tool_name, args)` | Record a tool invocation for later analysis |
| `check()` | Return a `DetectionResult` if a loop is found, else `None` |
| `reset()` | Clear all internal state |

## Built-in strategies

### ExactRepeatDetector

Detects when the last N tool calls share the same MD5 signature.  Each tool
call is hashed as `md5(json.dumps({"name": ..., "args": ...}, sort_keys=True))`.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `window` | 10 | Max recent signatures to retain in the sliding window |
| `threshold` | 3 | How many consecutive identical signatures trigger detection |

```python
from chimera.detection import ExactRepeatDetector

detector = ExactRepeatDetector(window=10, threshold=3)
detector.record("bash", {"command": "ls"})
detector.record("bash", {"command": "ls"})
detector.record("bash", {"command": "ls"})
result = detector.check()  # DetectionResult(detected=True, ...)
```

### PatternCycleDetector

Detects repeating A-B-A-B (or longer period) cycles.  The algorithm checks
every candidate period from 2 up to `len(history) // 2`.  A cycle is confirmed
when the same sub-sequence repeats `threshold` times consecutively at the tail.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `window` | 10 | Max recent signatures to retain |
| `threshold` | 2 | How many consecutive repetitions of the cycle constitute a match |

```python
from chimera.detection import PatternCycleDetector

detector = PatternCycleDetector(window=10, threshold=2)
detector.record("read_file", {"path": "a.py"})
detector.record("edit_file", {"path": "a.py"})
detector.record("read_file", {"path": "a.py"})
detector.record("edit_file", {"path": "a.py"})
result = detector.check()  # Cycle of period 2 repeated 2 times
```

### CompositeDetector

Fans out to a list of strategies.  `record()` is forwarded to **all** of them;
`check()` returns the first non-`None` result.

```python
from chimera.detection import CompositeDetector, ExactRepeatDetector, PatternCycleDetector

detector = CompositeDetector([
    ExactRepeatDetector(threshold=3),
    PatternCycleDetector(threshold=2),
])
```

## LoopDetector facade

The `LoopDetector` is a convenience wrapper that creates a default
`CompositeDetector` (with both `ExactRepeatDetector` and
`PatternCycleDetector`) and pairs it with an `OnDetect` action policy.

```python
from chimera.detection import LoopDetector, OnDetect

ld = LoopDetector(on_detect=OnDetect.BREAK, window=10, threshold=3)

result = ld.record_and_check("bash", {"command": "ls"})
if result:
    print(f"Loop: {result.pattern}")
```

## OnDetect enum

Controls what the agent loop does when a loop is detected:

| Value | Behaviour |
|-------|-----------|
| `OnDetect.ASK` | Prompt the user for confirmation before continuing |
| `OnDetect.BREAK` | Immediately stop the agent loop |
| `OnDetect.WARN` | Log a warning but continue execution |

## Custom strategies

Implement `DetectionStrategy` to add domain-specific detection:

```python
from chimera.detection import DetectionStrategy, DetectionResult

class TokenBudgetDetector(DetectionStrategy):
    def record(self, tool_name, args):
        ...
    def check(self):
        if self._over_budget():
            return DetectionResult(
                detected=True,
                strategy="token_budget",
                pattern="Token budget exceeded",
            )
        return None
    def reset(self):
        ...
```
