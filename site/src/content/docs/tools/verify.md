---
title: "verify_answer — Python boolean cross-check"
description: "Run a short Python snippet that prints True / False to verify a candidate answer. Useful in synthesis and math benchmarks."
---

`verify_answer` runs a Python snippet that should print `True` if a candidate answer is correct and `False` otherwise. It's a focused alternative to a full test suite when the check is single-shot.

## Schema

| Arg | Type | Required | Default | Description |
|---|---|---|---|---|
| `code` | string | yes | — | Python code that prints `True` or `False`. |
| `timeout` | integer | no | `30` | Wall-clock seconds before the snippet is killed. |

## Example invocation

```json
{
  "code": "answer = 42\nprint(answer == 6 * 7)"
}
```

```python
from chimera.tools.verify import VerifyAnswerTool

tool = VerifyAnswerTool()
result = tool.execute(
    {"code": "print(sum(range(11)) == 55)"},
    env=local_env,
)
print(result.metadata["verified"])  # True
```

## Output sample

```
True
```

`result.metadata["verified"]` is the parsed boolean. Anything other than a stripped-lowercase `true` lands as `False`.

## When to use it

- Math benchmarks (AIMO, MATH-500) — extract the integer answer and prove it via Python.
- Synthesis — quick sanity check before running the full suite.

## See also

- [`test`](./test.md) — full test suite runner.
- [`think`](./think.md) — record reasoning without running anything.
