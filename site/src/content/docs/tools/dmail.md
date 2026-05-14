---
title: "dmail — message your past self at a checkpoint"
description: "Save a checkpoint mid-run; later, rewind the conversation to that point and inject a short message. Useful when context fills up with irrelevant exploration."
---

`dmail` is a context-window trick. The agent saves a checkpoint at some point in the run, keeps exploring, and — if it learns something the past self should have known — rewinds to the checkpoint and injects a short message. Everything after the checkpoint is dropped; the message takes its place.

## Schema

| Arg | Type | Required | Description |
|---|---|---|---|
| `action` | string | yes | `checkpoint` or `send`. |
| `checkpoint_id` | integer | for `send` | Which checkpoint to rewind to. |
| `message` | string | for `send` | The note to inject in place of the dropped turns. |

## Example invocation

```json
{"action": "checkpoint"}
```

Returns the new checkpoint id, e.g. `Saved checkpoint 3.`

```json
{
  "action": "send",
  "checkpoint_id": 3,
  "message": "The bug is in retry.py line 88, not the loop. Skip the loop investigation."
}
```

## Output sample

```
Rewound to checkpoint 3 (dropped 14 turns). Injected message: 'The bug is in retry.py line 88...'
```

## When to use it

- The agent burned 10 turns ruling out a wrong hypothesis and now knows the real answer. Rather than carry the dead investigation forward (and pay tokens on every subsequent turn), `dmail` drops it.
- Pairs well with [`todo`](./todo.md) — the agent re-derives the next steps from a clean slate.

## Notes

- Checkpoints are per-session. They don't persist across `Session.fork()` boundaries.
- Compaction will still run if context grows — `dmail` is targeted; compaction is wholesale.

## See also

- [`todo`](./todo.md), [`think`](./think.md), [Compaction](/chimera/concepts/compaction/).
