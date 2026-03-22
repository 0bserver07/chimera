---
name: test-convergence
description: Iterate toward passing tests by tracking progress, rolling back regressions, and converging on a solution
triggers: ["test", "failing test", "make tests pass", "convergence", "TDD", "test-driven"]
---

When working toward making tests pass, follow a structured convergence loop that tracks progress and rolls back regressions. Do not randomly edit code and re-run — each iteration should make measurable progress.

## The Convergence Loop

1. **Establish the baseline.** Run the full test suite and record:
   - Total tests
   - Passing tests
   - Failing tests (list each one by name)
   - The pass rate (e.g., 37/42 = 88%)

   This is your baseline. Every iteration must improve on it or match it.

2. **Focus on one failure at a time.** Pick the simplest failing test (usually the one with the shortest error message or the fewest dependencies). Do NOT try to fix all failures at once.

3. **Read the failing test carefully.** Understand:
   - What is the test asserting?
   - What input does it provide?
   - What does the test expect vs what it got?
   - Is the test correct, or is the test itself wrong?

4. **Make the minimal fix.** Change only what is necessary to make this one test pass. Avoid refactoring or improving code in the same commit.

5. **Re-run the full suite after every fix.** Check:
   - Did the target test pass? (progress)
   - Did any previously passing tests break? (regression)
   - Did the overall pass rate improve?

6. **Roll back on regression.** If your fix causes other tests to fail, immediately revert. The fix is wrong — you need a different approach. Go back to step 3 and re-read the test with fresh eyes.

7. **Track convergence.** After each iteration, log the pass rate:
   ```
   Iteration 1: 37/42 (88%) — baseline
   Iteration 2: 39/42 (93%) — fixed test_parse_empty, test_validate_input
   Iteration 3: 39/42 (93%) — attempted test_connect_timeout, rolled back
   Iteration 4: 41/42 (98%) — fixed test_connect_timeout with different approach, test_retry
   ```

8. **Patience threshold.** If the pass rate has not improved for 3 consecutive iterations, stop making changes and re-investigate. Read the failing test's source file, its dependencies, and the test fixtures. You are probably missing context.

## Anti-patterns

- Fixing the test assertion instead of the code (unless the test is genuinely wrong)
- Making broad changes that fix one test but break three others
- Continuing to iterate without tracking progress
- Editing code you have not read fully
