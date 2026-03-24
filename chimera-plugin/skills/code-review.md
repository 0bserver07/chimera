---
name: code-review
description: Before committing, run multi-perspective code review and address all critical findings
triggers: ["review", "commit", "PR", "pull request", "check", "security", "bugs"]
---

Before committing changes or declaring a task done, run a multi-perspective code review. Self-review is unreliable -- you wrote the code and will confirm your own reasoning. Use the review tools to get an independent check.

## How to Review Your Changes

1. **Get the diff.** Run `git diff` (for unstaged) or `git diff --cached` (for staged) to capture what you changed.

2. **Send it through the review MCP.** Call the `chimera_review_diff` MCP tool with the diff text. It checks your changes from four perspectives:
   - **Logic:** Bare except clauses, None-unsafe chains, `== None` instead of `is None`, infinite loops without break conditions
   - **Security:** `eval()`, `exec()`, `shell=True`, hardcoded secrets, unsafe pickle/yaml, overly permissive chmod
   - **Architecture:** Deep relative imports, global mutation, too many parameters, wildcard imports
   - **Tests:** New public functions without corresponding test changes in the diff

3. **Read every finding.** Each finding has a severity (info, warning, error, critical), file location, category, and description.

4. **Fix all critical and error findings before committing.** These are blocking issues:
   - Hardcoded secrets or API keys
   - Shell injection via `subprocess(shell=True)`
   - Unsafe deserialization
   These are not suggestions -- they are real vulnerabilities.

5. **Address warnings where possible.** Bare except clauses, global mutation, and missing tests are not blocking but degrade code quality. Fix them if the fix is straightforward.

6. **Info findings are informational.** `TODO` comments, broad exception handlers, and enumerate suggestions are worth noting but do not need to block a commit.

## What the Severity Levels Mean

| Severity | Action Required | Examples |
|----------|----------------|---------|
| **CRITICAL** | Must fix before commit | Hardcoded passwords, API keys |
| **ERROR** | Must fix before commit | Shell injection, destructive SQL patterns |
| **WARNING** | Fix if straightforward | Bare except, global mutation, FIXME comments |
| **INFO** | Note and move on | TODOs, broad exception handlers, style suggestions |

## After Fixing

Re-run the review on the updated diff. Findings from the previous run may no longer apply, and your fixes may introduce new issues. Review is not a one-shot process -- iterate until no critical or error findings remain.

## When to Skip Review

Review is most valuable for:
- Changes to security-sensitive code (auth, permissions, secrets)
- Changes to public APIs or interfaces
- Changes spanning multiple files
- New features with complex logic

Review adds less value for:
- Documentation-only changes
- Renaming without logic changes
- Test-only additions (the tests themselves are the review)
