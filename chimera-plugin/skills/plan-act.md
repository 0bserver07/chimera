---
name: plan-act
description: Separate planning (read-only exploration) from execution (writes and commands) to avoid premature edits
triggers: ["plan", "complex task", "refactor", "large change", "multi-file", "architecture"]
---

For any task that touches more than one file or requires understanding existing code, split your work into two distinct phases. Never start editing until you have a plan.

## Phase 1: Plan (Read-Only)

During planning, use ONLY read-only operations. Do not write, edit, or run commands that modify state.

1. **Understand the request.** Restate the task in your own words to confirm understanding.

2. **Explore the codebase.** Use Read, Grep, and Glob to:
   - Find the files that will need to change
   - Read each file fully — understand the context, not just the grep match
   - Trace imports and dependencies to identify ripple effects
   - Check for existing tests that cover the code you will modify

3. **Identify constraints.** Before planning changes, understand what must NOT break:
   - Which tests currently pass for the affected modules?
   - Are there callers that depend on the current function signatures?
   - Are there configuration files or documentation that reference this code?

4. **Write the plan.** Output a numbered list of concrete steps:
   - Which files to modify, in what order
   - What the change is in each file (be specific: "add parameter `timeout` to `connect()`")
   - Which tests to update or create
   - What to verify after each step

## Phase 2: Act (Full Execution)

Now execute the plan step by step.

1. **Follow the plan order.** Modify files in the sequence you planned. This avoids breaking intermediate states.

2. **Verify after each step.** Run tests or linters after each file change, not just at the end. Catching errors early is much cheaper than debugging a chain of changes.

3. **Update the plan if needed.** If you discover something unexpected during execution, stop, update the plan, then continue. Do not improvise mid-stream.

4. **Handle deviations explicitly.** If a planned change turns out to be unnecessary or needs a different approach, note why before proceeding.

## When to Use This

- Any task involving 3+ files
- Refactoring (renaming, moving, restructuring)
- Adding a feature that touches multiple layers
- Fixing a bug where the root cause is unclear
- Any time you feel tempted to "just try something"
