---
name: loop-detection-signals
description: How to notice when a small model has begun looping, before the budget is gone.
triggers: ["loop", "stuck retry", "same call again", "going in circles", "spiral"]
---
## Loop-detection signals for small models

A small model in a tool-use loop rarely announces "I am stuck". It just
keeps emitting plausible-looking calls that produce no new information.
Catching the loop early is worth far more than any extra retry budget.

The signals, ranked by how reliable they are:

- **Tool-call repetition.** Two consecutive tool calls with byte-identical
  arguments is a hard stop. Even if the second call's *output* differed,
  re-issuing the same arguments without a new hypothesis means the model
  is hoping for a different answer. That is not a strategy; it is a coin
  flip.
- **Pattern cycles.** Three calls of the form `Read A → Edit A → Read A`
  with no `Bash` or test in between usually means the model is
  re-checking its own work instead of validating it externally. Break the
  cycle with a `pytest`, a `grep`, or a confirmation step.
- **Argument drift without rationale.** The model tweaks a single
  argument across attempts (`--quiet`, then `-q`, then `--silent`) without
  explaining why the previous attempt failed. This is guess-and-check
  dressed up as iteration.
- **Plan amnesia.** The plan from three turns ago no longer matches the
  current actions, but no replan happened. The model has drifted off the
  rails and is following the gradient of recent tokens instead.
- **Quoting yourself.** When a turn opens with "as I mentioned before"
  or restates a fact already in the transcript, the model is padding
  rather than progressing.

What to do when you spot a loop:

1. Stop the next tool call mid-thought. Do not "just try one more thing".
2. Write a 3-bullet summary of what was tried, what was observed, and
   what the gap is.
3. Choose one of: ask a focused user question, escalate to a larger
   model, or change strategy entirely. Do not resume the same approach.

The shrew loop has a cap on identical tool calls for exactly this
reason. If you feel the urge to bypass it, that is the urge the cap
exists to interrupt.
