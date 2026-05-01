---
name: incremental-edits
description: Make one small, verifiable change at a time instead of one large rewrite.
triggers: ["refactor", "rewrite", "big change", "many changes", "overhaul"]
---
## Incremental edits

Small models are most reliable when each `Edit` is small, focused, and
independently verifiable. They are least reliable when asked to land a
sweeping change in one shot. Treat every multi-step task as a sequence
of small commits-in-spirit, not a single diff.

**The unit of work:**

- One logical change per `Edit`. Renaming a variable is one change.
  Renaming a variable *and* extracting a helper *and* adjusting
  callers is three.
- Each unit ends in a verification step (a test, a lint pass, an
  import smoke check). If the verification fails, the unit is the
  whole rollback target — not the entire session.

**The shape of a good incremental sequence:**

1. Pick the smallest forward step that makes the codebase strictly
   better and still leaves it valid.
2. Apply it with one `Edit`.
3. Verify. If green, continue. If red, revert that one edit and
   reconsider — do not pile a fix on top of a broken state.
4. Repeat until the larger change is complete.

**Why this beats the rewrite approach:**

- A 200-line `Write` of a "cleaned up" version usually loses
  comments, imports, type annotations, and edge-case branches the
  original handled. None of those losses show up in the model's
  attention. They show up in the user's regression report.
- An incremental sequence keeps the codebase compilable at every
  step, so each verification is meaningful. A monolithic rewrite is
  green-or-red on the whole thing, which gives you no information
  about *which* part broke.
- Reviewers (human or model) can read 10 small diffs. They cannot
  meaningfully review one 800-line diff.

**Concrete heuristics:**

- If your `new_string` in an `Edit` is more than 40 lines, split the
  change. There are almost always two unrelated edits hiding in a
  big one.
- If you need to touch more than three files in a single step,
  re-read the `multi-file-edits` skill — and consider whether an
  intermediate compatibility shim would let you ship the change in
  two passes instead of one.
- When introducing a new helper, *first* add it (one edit), *then*
  switch one caller to it (one edit), *then* migrate the rest. Don't
  do all three in one shot.

**Anti-patterns:**

- "While I'm here" edits. If a typo or stylistic nit isn't part of
  the task, leave it. Drive-by changes are how scope creep lands
  in unreviewed commits.
- Bundling refactor + behaviour change in a single edit. Make the
  refactor first (no behaviour change, all tests still pass), *then*
  make the behaviour change.

Incremental work is slower per step and faster per task. The slow
parts are the diagnoses you no longer have to do.
