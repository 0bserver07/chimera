# Rerun on Failure

Rerun-on-failure is the load-bearing distinction between badger and
the other Chimera coding-agent CLIs. When the agent's first attempt
produces an unambiguous failure marker, the harness resets and retries
with a refined prompt up to `--max-reruns` extra attempts.

## Enable rerun

```bash
chimera badger -p "Fix the broken tests in tests/api/" \
  --rerun-on-failure --max-reruns 2
```

Total attempts = `1 + max_reruns`. With the default budget, that's up
to **three** attempts.

## What counts as a failure marker?

Detection is deliberately conservative. A false negative (a real
failure that slips past) is preferred over a false positive (rerunning
a healthy trajectory and burning more tokens).

The current marker list:

| Marker                   | Pattern                                      |
| ------------------------ | -------------------------------------------- |
| pytest test failure      | `^FAILED \S+::`                              |
| pytest summary           | `^=+\s*\d+\s+failed`                         |
| Python syntax error      | `^\s*SyntaxError\b`                          |
| Python traceback         | `^Traceback (most recent call last):`        |
| JavaScript syntax error  | `\bSyntaxError\b.*Unexpected`                |
| Rust compile error       | `^error\[E\d+\]`                             |
| Node assertion error     | `\bAssertionError(?:\s*\[ERR_\w+\])?:`       |
| Explicit failure marker  | `^(BUILD FAILED|TESTS FAILED|FAILURE\|ERROR:.*failed)` |

When **any** of these fire on the trajectory output, rerun is
triggered. When **none** fire, the result is returned as-is — even if
the agent reported `success=false`.

## Refined prompt

The next attempt's prompt is the original prompt prefixed by a short
directive that names the detected markers:

```
[badger rerun 1] The previous attempt produced pytest test failure,
Python traceback. Re-read the failing output, isolate the root
cause, fix it, and re-run any failing tests before reporting
completion. Do not claim done until verification passes.

Original task:
<original prompt>
```

This mirrors the harness-rewrite tradition's "verify before claiming
done" discipline.

## Inside the REPL

The `/rerun` slash command shows or sets the per-session budget:

```
> /rerun
/rerun: rerun_on_failure=False max_reruns=2
> /rerun on
/rerun: enabled (max_reruns=2)
> /rerun 5
/rerun: enabled, max_reruns=5
> /rerun off
/rerun: disabled
```

## API

The same machinery is exposed as a Python coroutine:

```python
from chimera.badger.rerun import run_with_rerun

result = await run_with_rerun(agent, prompt, env=env, max_reruns=2)
```

`run_with_rerun` accepts any object with an
`async_run(prompt, env=...) -> result` method, so it composes with the
canonical Chimera Agent and with custom subclasses.

## Why opt-in?

Rerun costs real tokens. The harness-rewrite posture treats that cost
as a budget, not a free safety net. Operators turn rerun **on** when
the workload warrants it (test fixes, code modernization), and leave
it **off** for one-shot exploratory queries.
