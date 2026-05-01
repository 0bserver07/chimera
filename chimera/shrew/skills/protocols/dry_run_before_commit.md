---
name: dry-run-before-commit
description: Always verify a change end-to-end before staging or committing it.
triggers: ["commit", "git add", "stage", "ready to commit", "ship it"]
---
## Dry-run before commit

The small-model failure mode at the end of a task is to declare
victory after a single successful `Edit` and stage the file without
ever running the code. The protocol below makes "ready to commit" a
mechanical check, not a feeling.

**The dry-run, in order:**

1. **Re-read the changed files.** Open them with `Read` and confirm
   the diff is what you expected. Small models occasionally produce
   `Edit` calls that succeed on the wrong region.
2. **Run the relevant test.** If you wrote one, run it. If a test
   already exists, run it. If the project has no tests for the area,
   run an import / smoke check (`uv run python -c "import mypkg.foo"`).
3. **Run the linter.** `uv run ruff check <path>` for Python. Whatever
   the project uses for the language at hand. Fix any warnings the
   change introduced; do not commit through them.
4. **Run the type checker** if the project uses one. `uv run mypy
   <path>`. Untyped code is invisible to the checker, but if your
   change touches typed code and silently strips annotations, mypy
   will say so.
5. **Run a wider test pass** if the change is non-trivial. A passing
   targeted test is necessary but not sufficient — the rest of the
   suite is the regression net.
6. **Inspect `git diff --staged`.** What is about to be committed,
   exactly. No surprise files, no debug prints, no `.pyc` or
   `.DS_Store`, no whitespace-only churn.

**Only after the six checks pass:**

- Stage the specific files (`git add path/to/file`, not `git add -A`).
- Write a commit message that names the *why*, not just the *what*.
- Wait for the user to greenlight the commit unless they have
  explicitly delegated commits to you.

**What to do when a check fails:**

- A failing test is a regression even if you didn't write the code
  that broke. Stop, diagnose, fix or revert.
- A new linter warning is a regression. Same rule.
- An unexpected file in the diff means your editor or tooling
  produced collateral. Unstage it before continuing.

**Anti-patterns:**

- Committing because "the build is green in CI". CI is a backstop, not
  a substitute for local checks.
- Squashing the failures into a follow-up commit. The follow-up rarely
  arrives, and the broken commit pollutes bisect.
- Trusting "I didn't change that file" — verify with `git status`.

Dry-run is cheap. A bad commit is expensive. Keep the asymmetry honest.
