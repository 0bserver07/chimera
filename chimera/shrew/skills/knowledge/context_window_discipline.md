---
name: context-window-discipline
description: How to keep a small local model's working context tight enough to reason well.
triggers: ["long context", "context full", "lost the thread", "what was I doing"]
---
## Context-window discipline for small models

A 9B–35B local model loses coherence well before its declared context is
full. Treat the *useful* window as roughly the first third of the *advertised*
window — past that, retrieval, attribution, and self-correction all degrade.

Concrete rules of thumb:

- Aim to keep the live conversation under ~8K tokens of *new* material
  (skills, prompt, transcript). If it grows past that, summarise older turns
  before continuing.
- Read files in slices, not in full. A 1,000-line module read in one shot
  burns context that you will need for the actual edit. Read 100–200 lines
  around the spot you care about, then expand only if the diff demands it.
- Quote sparingly. When you reference a function the user just showed you,
  cite the file path + line number rather than re-pasting the body.
- Prefer `grep`/`search` summaries over directory dumps. A file tree of 400
  entries is almost never worth the tokens; a `grep` for the symbol you
  actually need usually is.
- If the user keeps asking the same clarifying question, that is a context
  rot signal: summarise what you know, drop irrelevant earlier turns from
  your own working notes, and restart the plan from the summary.

Symptoms that you have already overrun the useful window:

- You start contradicting an earlier decision in the same session.
- You forget the name of a file you read three turns ago.
- Tool calls repeat with no new information.
- You hallucinate a function signature that diverges from the one you just
  read.

When you see those symptoms, *stop emitting tool calls* and emit a
condensation step instead: write a 5-bullet summary of the decisions made
so far, the open questions, and the next concrete action. Then proceed
from that summary as your new ground truth.
