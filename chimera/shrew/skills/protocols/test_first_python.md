---
name: test-first-python
description: Write or run a failing test before changing Python source.
triggers: ["python", "pytest", "fix the test", "fix bug", "broken test"]
---
## Test-first protocol for Python

When the task is "fix this bug" or "implement this function" in a Python
project, the small-model failure mode is to read source, guess at a
patch, and declare victory without ever running the test. The fix is to
let `pytest` be the source of truth.

The loop:

1. **Locate the test.** `grep -r "def test_<name>"` or look in `tests/`
   for a file mirroring the source path. If no test exists yet for the
   behaviour, write a minimal one that captures the bug *before* you
   touch source.
2. **Run it red.** `uv run pytest <path-to-test> -q -x`. Confirm the
   failure mode you expected. If the test passes already, your mental
   model of the bug is wrong — re-read the issue before patching.
3. **Patch.** Make the smallest change that could plausibly turn the
   test green. Touch only the file the failure points to unless the
   traceback says otherwise.
4. **Run it green.** Re-run the same `pytest` command. Read the *whole*
   output, not just the exit code — a test can pass while emitting a
   `DeprecationWarning` that signals you missed the real problem.
5. **Run the wider suite.** `uv run pytest <path-to-package> -q`. If
   anything else broke, you have a regression and must address it
   before claiming the task done.
6. **Lint and type-check.** `uv run ruff check <path>` and `uv run mypy
   <path>` if the project uses them. Small models often introduce unused
   imports or skip return type hints; the linters catch these for free.

Anti-patterns to avoid:

- Editing the test to match the broken code. If the test fails, the
  burden of proof is on the source, not the test, unless the test is
  obviously wrong. When the test is wrong, say so explicitly and ask the
  user.
- Running `pytest` once at the end. Run it after every meaningful edit
  so the loop stays tight and the diagnosis stays fresh.
- Adding `pytest.skip` or `xfail` to silence a failure. That's hiding
  the bug, not fixing it.

The whole point is to make "did this work?" a cheap, mechanical check
instead of a guess.
