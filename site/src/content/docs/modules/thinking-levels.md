---
title: "Thinking Levels"
description: "Thinking Levels"
---

`chimera.providers.thinking` provides a portable abstraction over extended
reasoning / thinking budgets so callers do not need to know provider-specific
parameters.

## ThinkingLevel enum

| Value | Token budget |
|-------|-------------|
| `OFF` | 0 (disabled) |
| `MINIMAL` | 1 024 |
| `LOW` | 2 048 |
| `MEDIUM` | 8 192 |
| `HIGH` | 16 384 |
| `MAX` | 32 768 |

`ThinkingLevel` extends `str`, so values compare equal to their string form
(`ThinkingLevel.HIGH == "high"`).

## budget_for_level()

```python
from chimera.providers.thinking import budget_for_level, ThinkingLevel

tokens = budget_for_level(ThinkingLevel.MEDIUM)  # 8192
```

Returns `0` for any level not in `THINKING_BUDGETS` (safe default).

## Provider mapping

Providers that support extended reasoning translate the budget to their native
parameters.  For Anthropic this maps to:

```json
{"type": "thinking", "budget_tokens": 16384}
```

## Usage

```python
from chimera.providers.thinking import ThinkingLevel
from chimera.providers.factory import create_provider

provider = create_provider("anthropic", model="claude-opus-4-5")
response = provider.complete(
    messages=messages,
    thinking=ThinkingLevel.HIGH,
)
```

Pass `ThinkingLevel.OFF` (the default) to disable extended reasoning entirely
and avoid the additional latency and token cost.
