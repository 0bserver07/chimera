---
name: smart-compaction
description: Manage context during long sessions -- summarize old turns, strip thinking blocks, extract key facts to persistent memory
triggers: ["context", "long session", "forgetting", "compaction", "memory", "too long", "running out", "window"]
---

Your context window is finite. In long sessions, you will start forgetting earlier findings, re-reading files you already read, and contradicting your own decisions. Actively manage what stays in context.

## What Chimera Does Automatically

If configured, Chimera runs a compaction pipeline when your context reaches 70% capacity:

1. **ThoughtStripCompaction** strips `<thinking>` blocks from older assistant messages, reclaiming 30-50% of context consumed by extended thinking. Only the last 2 assistant messages keep their thinking content.

2. **SmartCompaction** replaces messages older than the last 10 turns with a condensed summary block. Recent messages stay verbatim so you have full context for the current work.

3. **PersistentMemory** extracts factual statements from the conversation every 5 turns and stores them in a JSON file. When a new session starts, these facts are re-injected as context so you do not lose project knowledge across session resets.

If context reaches 90%, an emergency hard reset keeps only the system prompt and the last 5 messages.

## What You Should Do

### 1. Summarize Proactively

After investigating a complex area, compress your findings before moving on. Keep:
- File paths (you can re-read if needed)
- Key function signatures and their behavior
- Decisions made and their rationale
- Remaining work items

Discard:
- Full file contents you read but only needed a few lines from
- Search results that were not relevant
- Error messages from problems you already fixed
- Implementation details of code you are not changing

### 2. Maintain a Working State

For multi-step tasks, keep a running summary of your progress:
- **Goal:** What you are trying to accomplish
- **Completed:** Steps done and their outcomes
- **Key facts:** Critical discoveries (e.g., "auth.py uses JWT, not session cookies")
- **Next:** The immediate next step

Update this after every major milestone, not after every tool call.

### 3. Re-read Instead of Remembering

If you need details about a file you read earlier, re-read it. One tool call is cheap. Working from a faulty memory is expensive -- wrong changes lead to wasted retry cycles.

### 4. Extract Important Facts Explicitly

When you discover something that should survive compaction, state it clearly as a factual sentence. The memory extraction heuristic looks for declarative statements containing words like "is", "uses", "has", "requires", "provides". For example:
- "The project uses SQLAlchemy for database access" (will be extracted)
- "Hmm, I think maybe the database..." (will not be extracted)

### 5. Know When Context Is Getting Stale

Signs that context has degraded:
- You are asking questions you already answered earlier
- You are re-reading files you read in the last 20 turns
- Your responses are getting less specific or more hedging
- You are making changes that conflict with earlier decisions

When this happens, stop and explicitly summarize everything you know. Treat it as a fresh start with pre-loaded knowledge.

## Context Budget Rule of Thumb

In a 200K context window:
- System prompt + tools: ~10K tokens
- Each turn (user + assistant + tool calls): ~2K-5K tokens
- Thinking blocks: ~1K-5K tokens per turn when enabled
- You can sustain ~30-40 turns before compaction is needed
- After compaction, you get another ~20-30 turns
