---
name: context-management
description: Manage your context window efficiently — summarize what you have learned, discard irrelevant details, and keep the essential information accessible
triggers: ["context", "long conversation", "too much", "summarize", "remember", "forgot"]
---

Your context window is finite. On long tasks, managing what you keep in active memory is as important as the work itself. Without active management, you will lose track of earlier findings and repeat work.

## Context Management Strategies

### 1. Summarize as You Go

After reading a file or completing an investigation, compress your findings into a brief summary. Do not keep the full file content in memory — keep only:
- The file path (so you can re-read if needed)
- The key facts: function signatures, important logic, relevant line numbers
- Your conclusions: what this means for the current task

Example: Instead of remembering 200 lines of `auth.py`, remember:
"auth.py: `authenticate(token: str) -> User` at line 45. Calls `db.find_user()`. Raises `AuthError` on invalid token. No rate limiting."

### 2. Maintain a Working Summary

For tasks that span many steps, maintain a running summary of what you know:
- **Goal:** One sentence describing what you are trying to accomplish
- **Completed:** Numbered list of completed steps and their outcomes
- **Key facts:** Critical information discovered during investigation
- **Remaining:** Steps still to do

Update this summary after every major milestone, not after every tool call.

### 3. Discard Aggressively

Not everything you read matters. After exploring a file, ask: "Will I need this information again?" If not, do not summarize it — let it go. Specifically discard:
- File contents you read but found irrelevant
- Search results that did not match what you needed
- Error messages from problems you already fixed
- Implementation details of code you are not modifying

### 4. Re-read Instead of Remembering

If you need details about a file you read earlier, re-read it rather than trying to remember. Re-reading is cheap (one tool call). Working from a faulty memory is expensive (wrong changes, wasted iterations).

### 5. Batch Related Information

When investigating multiple files, group your findings by topic rather than by file:
- "Authentication flow: auth.py:45 -> db.py:120 -> session.py:30"
- "Error handling: all three files catch ValueError but handle it differently"

This is more useful than separate per-file summaries because it reveals relationships.

## When Context Is Running Low

If you notice your responses are getting less precise or you are asking questions you already answered:
1. Stop and explicitly summarize everything you know about the current task
2. List the files you have modified and what changes you made
3. Identify the next concrete step
4. Continue from that summary — treat it as a fresh start with pre-loaded knowledge
