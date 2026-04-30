---
name: scaffold-model-fit
description: The scaffold-model-fit principle — match the harness to the model's capability ceiling.
triggers: ["why is the agent failing", "model can't follow", "tool soup", "scaffold"]
---
## Scaffold-model-fit

A coding agent has two halves: the **model** that does the reasoning and the
**scaffold** (tools, prompts, control flow) it operates inside. Frontier
models forgive a sloppy scaffold; small local models do not. Most "the model
is bad" complaints are actually "the scaffold is too rich for this model".

The fit dimensions to think about:

1. **Tool count.** A 9B model with 20 tools spends most of its budget
   guessing which tool to call. Trim to the 4–6 tools the task actually
   needs. `Read`, `Edit`, `Bash`, `Grep`, sometimes `Write` is plenty for
   most code work.
2. **Argument shape.** Small models confuse positional args, drop required
   fields, and re-invent option names. Prefer tools whose schema has 2–3
   named arguments with obvious types. Punish or reject malformed calls
   instead of silently retrying — the model needs the signal.
3. **Prompt structure.** Frontier models can absorb a 10K-token system
   prompt of personas, policies, and examples. Small models do better with
   a short policy block at the top and *just-in-time* skill snippets
   pulled in only when relevant.
4. **Reasoning surface.** When you can, give the small model a place to
   think out loud (a scratchpad, an explicit "plan" message) before it has
   to commit to a tool call. The reasoning quality after a 30-token plan
   is markedly better than a tool call emitted cold.
5. **Recovery loops.** Frontier models can self-correct after 4 or 5 bad
   tool calls. Small models tend to dig deeper. Cap retries at 2 and force
   a step-back/re-plan rather than letting the loop spiral.

The shrew defaults — small toolset, short system prompt, condensation on
context pressure, retry caps, explicit plan steps — exist to keep the
scaffold inside the model's competence envelope. Resist piling on tools
"just in case"; every extra tool spends capability that your real task
needs.
