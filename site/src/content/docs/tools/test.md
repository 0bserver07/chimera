---
title: "test — run the project test suite"
description: "Invoke the environment's canonical test runner. Returns parsed pass/fail counts when available."
---

`test` calls the environment's canonical test runner (typically `pytest` for Python projects). If a `path` is given, that path is passed through. Otherwise the env's `run_tests()` method is invoked, which discovers a sane default for the language.

## Schema

| Arg | Type | Required | Description |
|---|---|---|---|
| `path` | string | no | Specific test file or directory to run. Omit to run the full suite. |

## Example invocation

```json
{"path": "tests/test_agent.py"}
```

```python
from chimera.tools.test import TestTool

tool = TestTool()
result = tool.execute({}, env=local_env)
print(result.metadata)  # {'passed': 3922, 'failed': 0, 'errors': 0, 'total': 3922}
```

## Output sample

```
============================= test session starts =============================
collected 3922 items
...
============================= 3922 passed in 41.3s =============================
```

`result.metadata` carries parsed totals when the runner emits them. On failure, `result.error` reads `Tests failed (exit code N)` and the captured output is still in `result.output`.

## See also

- [`bash`](./bash.md) — the lower-level alternative.
- [`verify_answer`](./verify.md) — single-shot Python boolean check.
