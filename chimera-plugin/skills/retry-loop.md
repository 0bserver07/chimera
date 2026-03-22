---
name: retry-loop
description: When a fix doesn't work, undo and try a fundamentally different approach instead of iterating on the same idea
triggers: ["fix", "bug", "failing test", "retry", "still failing", "doesn't work"]
---

When your first attempt to fix a bug or failing test does not work, do NOT make small tweaks to the same approach. The most common failure mode is trying variations of the same wrong idea. Instead, follow this structured retry protocol.

## The Retry Protocol

1. **Stop and undo.** Revert your changes completely before trying again. Use `git checkout -- <file>` or re-read the original file content. You need a clean starting point, not a pile of half-fixes.

2. **Score what happened.** Before retrying, explicitly assess the previous attempt:
   - Did the error message change? (progress)
   - Did new tests break that were passing before? (regression — hard stop)
   - Is the error in the same location or a different one? (different root cause)

3. **Diagnose a DIFFERENT root cause.** Write out at least two alternative hypotheses for why the code is failing. The correct fix almost always addresses the root cause, not the symptom. Common root cause categories:
   - Wrong assumption about input data (type, shape, nullability)
   - Missing initialization or setup step
   - Incorrect order of operations
   - Stale state from a previous operation
   - Wrong scope (variable shadowing, closure capture)

4. **Try a completely different approach.** Not a variation — a fundamentally different strategy. If you tried adding a null check, maybe the real fix is ensuring the value is never null. If you tried catching an exception, maybe the real fix is preventing it.

5. **Verify with tests after each attempt.** Run the relevant test suite, not just the one failing test. Regressions in other tests mean your fix is wrong even if the target test passes.

6. **Know when to stop.** After three fundamentally different attempts, step back and re-read the surrounding code. You are probably missing context. Read the caller, the test setup, and the data flow into the failing function.

## Anti-patterns to Avoid

- Adding `try/except: pass` to silence errors
- Tweaking the same line repeatedly with slight variations
- Fixing the test assertion instead of the code under test
- Adding special-case `if` branches instead of fixing the general logic
