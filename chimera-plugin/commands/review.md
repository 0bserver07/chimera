---
name: review
description: Run multi-agent code review on staged changes or a specified diff
---

Review the current git diff using multiple specialized perspectives.

## Steps

1. **Get the diff.** Run `git diff --staged` first. If empty, fall back to `git diff`. If both are empty, ask the user what to review.

2. **Identify changed files.** Parse the diff to list every modified file. Read each changed file in full to understand the surrounding context — never review a diff in isolation.

3. **Analyze from four perspectives:**

   **Logic review:**
   - Off-by-one errors, boundary conditions, null/None handling
   - Race conditions or shared mutable state
   - Error handling: are exceptions caught too broadly? Are errors silenced?
   - Return value correctness — does every code path return the right type?

   **Security review:**
   - Injection vectors (SQL, command, path traversal)
   - Authentication and authorization gaps
   - Secrets or credentials in code or config
   - Unsafe deserialization, eval(), or dynamic imports

   **Test coverage review:**
   - Are there tests for the changed code? Check for corresponding test files.
   - Do existing tests still cover the modified behavior?
   - Are edge cases tested (empty input, large input, error paths)?

   **Architecture review:**
   - Does the change follow existing project patterns and conventions?
   - Naming consistency with the rest of the codebase
   - Separation of concerns — is business logic mixed with I/O?
   - Are there new dependencies that should be optional?

4. **Present findings.** Group by severity:
   - **Critical:** Must fix before merge (bugs, security issues)
   - **Warning:** Should fix (test gaps, code smells)
   - **Info:** Consider improving (style, naming, minor refactors)

   Include file path and line number for every finding.

5. **Summarize.** End with a one-line verdict: APPROVE, REQUEST CHANGES, or NEEDS DISCUSSION.
