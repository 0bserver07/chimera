---
title: "cron_list — list scheduled jobs"
description: "Dump the contents of ~/.chimera/cron/jobs.json. Read-only and safe to call frequently."
---

`cron_list` returns the contents of the cron store as pretty-printed JSON. The tool is marked `is_read_only` and `is_concurrency_safe`, so it can run alongside other tools without permission gating.

## Schema

No arguments.

## Example invocation

```json
{}
```

```python
from chimera.tools.cron_tools import CronListTool

tool = CronListTool()
result = tool.execute({}, env=None)
print(result.output)
```

## Output sample

```json
[
  {
    "name": "daily-sync",
    "schedule": "0 9 * * *",
    "command": "uv run python scripts/sync.py",
    "env": {"PYTHONPATH": "/Users/me/dev/chimera"},
    "registered": true
  },
  {
    "name": "nightly-bench",
    "schedule": "0 2 * * *",
    "command": "uv run python examples/benchmarks/humaneval_full.py",
    "env": {},
    "registered": false
  }
]
```

An empty store returns `[]`.

## See also

- [`cron_create`](./cron-create.md), [`cron_delete`](./cron-delete.md).
