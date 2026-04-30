---
name: error-recovery
description: Bounded recovery protocol when a tool call or command fails.
triggers: ["error", "exception", "command failed", "stack trace", "traceback"]
---
## Error-recovery protocol

When a tool call or shell command fails, the small-model default is to
retry with cosmetic variations and slowly drift further from the goal.
The protocol caps recovery at three steps and forces a human-readable
diagnosis between attempts.

The loop, in order:

1. **Read the error.** All of it. Not just the last line. Stack traces
   carry the file path and line number you need; the message above the
   trace usually names the wrong assumption. If you cannot articulate
   what the error means in plain English, you are not yet ready to
   retry.
2. **Form one hypothesis.** Write it down (in the assistant turn, not
   silently): "I think this failed because <X>." A single concrete
   hypothesis is far better than a list of three vague ones.
3. **Make the smallest change that tests the hypothesis.** Often that's
   re-reading a file, adjusting one argument, or running `--help` to
   verify a flag exists.
4. **Retry once.** If it works, write a one-line note about the cause so
   the user has a paper trail.
5. **If it still fails, stop and re-plan.** Do not try a third retry on
   the same hypothesis. Either:
   - the hypothesis is wrong → revise it based on what you saw and try
     once more from step 3, or
   - you are out of budget → summarise the failure for the user and ask
     a focused question (see the `one-focused-question` skill).

Hard caps: never retry the same tool call with the same arguments more
than twice. If the third attempt is identical to the first, you are in a
loop. Break out and re-plan.

Specific recoveries worth memorising:

- `Edit` "string not found" → re-`Read` the file, copy the literal text
  including whitespace, retry once.
- `Edit` "string is not unique" → widen `old_string` with two extra
  lines of context, retry once.
- `Bash` "command not found" → check whether the project uses a
  wrapper (`uv run`, `npm run`, `make`) before assuming the tool is not
  installed.
- `Bash` non-zero exit with no output → re-run with `-v`, `--verbose`,
  or `2>&1` to capture stderr.
- HTTP failures from `WebFetch` → check the URL exactly, don't retry
  immediately on transient 5xx (the user can wait), prefer reading
  cached docs locally if the project ships them.

The protocol is boring on purpose. Boring is what stops a small model
from spiralling into unproductive retry loops.
