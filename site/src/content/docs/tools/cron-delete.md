---
title: "cron_delete — remove a scheduled job"
description: "Delete a cron entry by name and unregister it from the host scheduler if it was registered."
---

`cron_delete` removes a job from `~/.chimera/cron/jobs.json` and (on macOS) unloads its `launchd` plist if it was registered. It's marked `is_destructive`, so the [`Permissions`](/chimera/concepts/permissions/) layer should gate it in production setups.

## Schema

| Arg | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | The job name to delete. |

## Example invocation

```json
{"name": "daily-sync"}
```

```python
from chimera.tools.cron_tools import CronDeleteTool

tool = CronDeleteTool()
result = tool.execute({"name": "nightly-bench"}, env=local_env)
```

## Output sample

```
Deleted job 'daily-sync'. Unregistered launchctl entry com.chimera.cron.daily-sync.
```

## Errors

| Message | Cause |
|---|---|
| `Job '<name>' not found` | The name wasn't in the store. |

## See also

- [`cron_create`](./cron-create.md), [`cron_list`](./cron-list.md).
