---
title: "cron_create — schedule a recurring job"
description: "Persist a scheduled job to ~/.chimera/cron/jobs.json. Optionally register it with the host scheduler (launchctl on macOS)."
---

`cron_create` saves a recurring job (name + cron expression + command) to the per-user store. On macOS, pass `register: true` to also drop a `launchd` plist into `~/Library/LaunchAgents/`. The store is plain JSON so it's editable by hand and survives Chimera upgrades.

## Schema

| Arg | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | string | yes | — | Unique job name. |
| `schedule` | string | yes | — | 5-field cron expression (`min hour dom mon dow`). |
| `command` | string | yes | — | Shell command to run. |
| `env` | object | no | `{}` | Optional env vars (string-keyed string values). |
| `register` | boolean | no | `false` | If `true` on macOS, register via `launchctl`. |

## Example invocation

```json
{
  "name": "daily-sync",
  "schedule": "0 9 * * *",
  "command": "uv run python scripts/sync.py",
  "env": {"PYTHONPATH": "/Users/me/dev/chimera"},
  "register": true
}
```

```python
from chimera.tools.cron_tools import CronCreateTool

tool = CronCreateTool()
result = tool.execute(
    {"name": "nightly-bench",
     "schedule": "0 2 * * *",
     "command": "uv run python examples/benchmarks/humaneval_full.py"},
    env=local_env,
)
```

## Output sample

```
Created job 'daily-sync' (0 9 * * *).
Registered with launchctl as com.chimera.cron.daily-sync.
```

## Errors

| Message | Cause |
|---|---|
| `Job '<name>' already exists` | Pick a different name or delete the existing job. |
| `Invalid cron expression: ...` | The schedule didn't parse. |
| `launchctl not available on this platform` | `register: true` was set on Linux / Windows. |

## See also

- [`cron_list`](./cron-list.md), [`cron_delete`](./cron-delete.md).
