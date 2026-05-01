---
name: bisect-on-failure
description: When a regression appears, bisect the change set instead of guessing which edit caused it.
triggers: ["regression", "bisect", "broken after", "worked yesterday", "what broke this"]
---
## Bisect on failure

When a previously-passing test starts failing, or a previously-working
command starts erroring, the small-model temptation is to inspect the
*current* code and reason from there. That works only if the code is
small. When the change set is more than a couple of files, bisecting is
faster and far more reliable.

**The protocol when you have git history:**

1. Confirm a known-good commit. Either `git log` for a recent commit
   the user remembers as working, or a tag, or `origin/main`.
2. Confirm the failing commit (usually `HEAD`).
3. Run `git bisect start`, `git bisect bad <failing>`, `git bisect good
   <known-good>`. Git will check out a midpoint commit.
4. Run the reproducing command (a single test, ideally). Mark `git
   bisect good` or `git bisect bad` based on the result.
5. Repeat until git names the offending commit.
6. `git bisect reset` to leave the tree where you started.

For a 50-commit range, bisect resolves to the bad commit in roughly 6
steps. That is far less work than reading 50 diffs.

**The protocol when you have no git history (or the regression is in
your own session):**

1. Note the last known-working state. Often that's the file before
   your last `Edit`.
2. Revert the most recent change. Re-run the test.
3. If it passes, the regression is in that change. Diff the old and
   new versions and stare at the small delta.
4. If it still fails, revert the next-most-recent change and continue
   backward.

**Anti-patterns:**

- Reading the whole codebase looking for "what looks suspicious". The
  regression is in the *delta*, not the static surface area.
- Reverting all your edits at once and starting over. You lose the
  information that would have told you which edit was the culprit.
- Skipping the reproducer. If you can't reproduce the failure on the
  bad commit, your "good" and "bad" labels are noise.

**Useful invocations:**

- `git bisect run <command>` — automated bisect, where `<command>`
  exits 0 on good and non-zero on bad. The fastest possible diagnosis
  on a deterministic failure.
- `git bisect skip` — when the midpoint commit is broken for an
  unrelated reason and can't be tested.

Bisect is the most underused debugging tool in a small model's
repertoire. Reach for it before you reach for guesswork.
