---
name: investigate-first
description: Always investigate the codebase before making changes — read the relevant code, trace the call chain, and understand the design intent
triggers: ["investigate", "understand", "explore", "before changing", "how does this work", "why"]
---

Never modify code you have not read and understood. The most expensive bugs come from changes made without understanding the surrounding context.

## The Investigation Protocol

1. **Read the target file in full.** Do not jump to the specific function — read the whole file. You need to understand:
   - What module is this? (check the module docstring and imports)
   - What else lives in this file? (sibling functions reveal design patterns)
   - What are the dependencies? (imports show coupling)

2. **Trace the callers.** Search for all places that call the function or use the class you are about to modify:
   ```
   Grep for the function name across the codebase
   ```
   Read at least the top 3 callers in full. They tell you:
   - What arguments are actually passed (not just what the signature allows)
   - What return values are expected
   - Whether there are implicit contracts (e.g., callers assume the list is sorted)

3. **Trace the callees.** Read the functions that your target calls. Understand:
   - What exceptions they can raise
   - What side effects they have
   - Whether they are stateful (do they modify shared state?)

4. **Check the tests.** Find and read the existing tests for this code:
   - What behaviors do the tests verify?
   - What edge cases are covered?
   - Are there test fixtures that set up specific state?

5. **Understand the design intent.** Before changing anything, answer:
   - Why was the code written this way? (check git blame if the reason is unclear)
   - What invariants does it maintain?
   - Is this pattern used elsewhere in the codebase?

## Signs You Have Not Investigated Enough

- You are changing code and you cannot explain what the function does
- You do not know who calls the function you are modifying
- You are surprised by a test failure after your change
- You are adding a parameter without checking if callers will pass it

## The 5-Minute Rule

If you cannot explain the code you are about to change in plain language within 5 minutes of reading it, you have not read enough context. Keep tracing the dependency chain until you can.
