---
name: benchmark
description: Run a coding benchmark against the current codebase to measure agent effectiveness
---

Run a structured benchmark to evaluate how well the current coding approach is working.

## Steps

1. **Identify scope.** Ask the user what to benchmark, or default to the most recently changed module. Use `git log --oneline -10` to find recent activity.

2. **Collect baseline metrics.** Run the test suite and record:
   - Total tests, passing tests, failing tests
   - Test execution time
   - Lint error count (run `ruff check --statistics` or the project's configured linter)

3. **Analyze code quality signals:**
   - Count TODO/FIXME/HACK comments in changed files
   - Check type coverage if mypy is configured (`mypy --stats`)
   - Measure function complexity (functions over 50 lines, deeply nested logic)

4. **Generate a scorecard.** Present results as a table:
   | Metric | Value | Status |
   |--------|-------|--------|
   | Tests passing | 142/145 | Warning |
   | Lint errors | 3 | Warning |
   | Type coverage | 87% | OK |
   | TODOs in scope | 2 | Info |

5. **Suggest improvements.** For each non-OK metric, suggest a concrete next step.

Do NOT modify any files. This is a read-only analysis command.
