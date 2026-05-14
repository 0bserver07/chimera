---
title: "bash — run a shell command"
description: "Execute a shell command in the environment, with a configurable timeout. Output is stdout + stderr; non-zero exit codes are surfaced as errors."
---

`bash` runs a shell command inside the active [`Environment`](/chimera/concepts/environments/) (local, docker, remote, etc.). stdout and stderr are returned together. A non-zero exit code is reported via the `error` field; the captured output is still available.

## Schema

| Arg | Type | Required | Default | Description |
|---|---|---|---|---|
| `command` | string | yes | — | The command to execute (passed to the env's shell). |
| `timeout` | integer | no | `120` | Wall-clock seconds before the command is killed. |

## Example invocation

```json
{"command": "ls -la chimera/", "timeout": 30}
```

```python
from chimera.tools.bash import BashTool

tool = BashTool()
result = tool.execute({"command": "uv run pytest -x"}, env=local_env)
print(result.output)
```

## Output sample

```
total 24
drwxr-xr-x  8 yad  staff   256 May 14 12:00 .
drwxr-xr-x 15 yad  staff   480 May 14 11:00 ..
-rw-r--r--  1 yad  staff   312 May 14 09:00 __init__.py
...
```

On a non-zero exit:

```
<stdout>
STDERR:
<stderr>
```

with `result.error = "Exit code 1"`.

## Notes

- The command runs in the env's working directory.
- For risky commands (`rm -rf`, `kill`, etc.), wire a permission policy at the [`LoopConfig`](/chimera/concepts/loop-config/) level.
- Use [`test`](./test.md) for canonical "run the test suite" calls; `bash` is the catch-all.

## See also

- [`test`](./test.md), [`git`](./git.md), [`search`](./search.md)
