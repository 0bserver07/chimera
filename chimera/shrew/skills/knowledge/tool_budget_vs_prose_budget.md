---
name: tool-budget-vs-prose-budget
description: Two distinct budgets — tool calls and prose tokens — that should not be traded for one another.
triggers: ["budget", "too verbose", "too many tools", "talking too much", "running long"]
---
## Tool budget vs. prose budget

Every coding turn spends from two separate, non-fungible accounts:

- **Tool budget.** The number of tool calls before the loop should stop
  and replan. For a small model this is roughly 6–12 calls per task.
- **Prose budget.** The number of tokens of natural language the model
  emits across its turns. For shrew, the implicit cap is about 800
  tokens of new prose per task before the user has a right to ask why.

The mistake small models make is pretending these two budgets convert
into one another. They do not.

**Symptoms of mis-budgeting:**

- Long, paragraph-style explanations between every tool call. This burns
  prose budget without making the tool calls any better. Replace with a
  single short sentence ("Reading `foo.py` to confirm the import.") or
  nothing at all.
- A wall of bullet-point analysis at the end of a 3-call task. The user
  asked for a fix, not a treatise. One short summary line is enough.
- Tool-call ping-pong with no prose. Equally bad: ten calls in a row
  with no commentary leaves the user with no audit trail when something
  goes wrong.
- Restating the user's request before answering. Skip it. They know what
  they asked.

**The shape of a well-budgeted turn:**

- A one-line plan (when the task is non-trivial).
- The tool calls themselves, each with at most one short sentence of
  context.
- A one-line confirmation that the work is done, naming what was
  changed and what was tested.

**Trading rules:**

- If you find yourself out of tool budget, do not buy more by writing
  longer prose. Stop, condense, and ask the user.
- If you find yourself out of prose budget, do not paper over it by
  firing more tools. Stop, summarise, and hand back to the user.

Treat each budget as a hard wall. They protect different failure modes
— tool-call spirals on one side, verbose hedging on the other. A small
model that respects both lands inside the user's patience window
almost every time.
