---
name: escalation-signals
description: When to stop fighting a small model and escalate to a larger one.
triggers: ["stuck", "give up", "escalate", "switch model", "bigger model"]
---
## When to escalate to a bigger model

Small local models save money and latency, but there is a class of tasks
where the right move is to admit the ceiling has been hit and route to a
larger cloud model. Recognising the signals early saves more time than any
number of clever retries.

Escalate when you see two or more of these in the same task:

- **Three failed retries on the same tool call.** Same error, same
  argument shape, same fix attempted. The model has converged on a wrong
  mental model and will not recover by itself.
- **Spec-level reasoning required.** The task hinges on understanding why
  a piece of code exists, what invariant it preserves across files, or how
  a refactor will interact with consumers you have not read. Pattern
  matching alone won't get there.
- **Long-range cross-file dependencies.** The change requires holding
  three files of context simultaneously and reasoning about their
  interaction. Small models drop one of the three almost every time.
- **Subtle correctness traps.** Off-by-one in an algorithm, race
  conditions in concurrent code, anything where the obvious answer is
  wrong. These are exactly where small models confidently produce broken
  code.
- **The user explicitly asks for the best answer.** A real review, a
  release-blocker bug, or a security-relevant change — the cost of a
  bigger model is dwarfed by the cost of being wrong.

Stay on the small model when:

- The task is mechanical: rename, format, add a docstring, write a
  one-liner test.
- The change is local to a single file and a few lines.
- You are exploring or sketching and will iterate anyway.
- Latency matters more than peak quality (interactive shell loops, watch
  modes).

The honest signal you have hit the ceiling is when you stop being able to
*describe* why your last attempt failed. If you can articulate the gap, you
can probably close it on the small model. If you can't, escalate.
