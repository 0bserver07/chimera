---
name: focus-chain
description: Maintain a chain of focus through multi-step tasks — always know what you are doing, what you just did, and what comes next
triggers: ["multi-step", "lost", "where was I", "context", "complex", "sequence"]
---

When working on a multi-step task, maintain an explicit focus chain so you never lose track of where you are. The biggest failure mode in long tasks is forgetting the current objective and drifting into tangential work.

## The Focus Chain

At all times, keep three things in your working memory:

1. **Current focus:** The specific sub-task you are working on right now.
2. **Previous result:** What you just completed and its outcome.
3. **Next step:** What you will do after the current focus is done.

## How to Maintain Focus

1. **Decompose before starting.** Break the task into a numbered list of concrete steps. Each step should be completable in 1-5 tool calls. If a step needs more, decompose it further.

2. **Announce transitions.** When you finish one step and move to the next, explicitly state:
   - "Completed: [what you just did]"
   - "Result: [what happened — success, partial, or failure]"
   - "Next: [what you are doing now and why]"

3. **Check alignment after every tool result.** After reading a file or running a command, ask yourself: "Does this result change my plan?" If yes, update the plan before continuing. If no, proceed to the next action.

4. **Do not follow tangents.** If you discover something interesting but unrelated to the current focus:
   - Note it briefly (one line) for later
   - Do NOT investigate it now
   - Return to the current step

5. **Recover from interruptions.** If you get an unexpected error or confusing result:
   - Re-read the current step in your plan
   - Re-read the last 2-3 tool results
   - Decide whether to fix the issue (if it blocks progress) or skip it (if it doesn't)

## Signs You Have Lost Focus

- You are reading files that are not related to any step in your plan
- You are making edits you did not plan
- You cannot explain in one sentence what you are currently trying to do
- You have been working on the same step for more than 10 tool calls

## Recovery

If you realize you have lost focus:
1. Stop all activity
2. Re-read the original task
3. List what you have completed so far
4. Identify the next incomplete step
5. Resume from that step
