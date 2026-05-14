# Build SWE-Agent in 60 Lines

This is the headline tutorial. By the end you'll have a working coding agent — the kind that reads a failing test, locates the bug, edits the source, runs the test again, and stops when it passes — built from five chimera primitives in **~60 lines** of agent code.

The upstream we're modelling is SWE-Agent: a research scaffold that earned its reputation on SWE-bench by stripping a coding agent down to the bare minimum — a few tools, a terse system prompt, and a ReAct loop with a "stop when tests pass" exit condition. Chimera ships that posture as a preset, but this tutorial walks the constructor so you can see every block.

## 0. What makes SWE-Agent SWE-Agent

Three design choices define the posture:

1. **Minimal tool set** — read / write / edit / bash. No web search, no notebook tools, no scratchpad. Each tool does one thing and the agent must use them deliberately.
2. **Tests are the oracle** — the system prompt instructs the agent to keep iterating until the test command exits zero. There's no "I'm done" judgement call by the model; the test runner decides.
3. **ReAct loop** — reason, act (tool call), observe (tool result), repeat. No tree-of-thought, no plan-then-execute split. Just one loop.

Everything else (sub-agents, compaction, hooks, transcripts) is omitted. That's the point.

## 1. Five blocks

A coding agent in chimera is the sum of five things, all of which are independent and swappable:

| Block | Role | Class |
|---|---|---|
| Provider | The LLM | `chimera.create_provider(...)` |
| Tools | What the agent can do | any `BaseTool` |
| Prompt | System instructions | `chimera.Prompt` |
| Loop | Reasoning strategy | `chimera.ReAct` |
| Environment | Sandbox / cwd | `chimera.LocalEnvironment` |

The constructor stitches them together:

```python
import chimera

agent = chimera.Agent(
    provider=provider,
    tools=tools,
    loop=loop,
    prompt=prompt,
    name="swe-agent-clone",
)
```

## 2. Build the agent

```python
# 1. Provider — any LLM with tool calling. Here, Ollama Cloud.
import os
os.environ.setdefault("OLLAMA_HOST", "https://ollama.com")

import chimera
from chimera.core.loop_config import LoopConfig
from chimera.tools.bash import BashTool
from chimera.tools.edit import EditFileTool
from chimera.tools.read import ReadFileTool
from chimera.tools.write import WriteFileTool

provider = chimera.create_provider(
    provider_type="ollama",
    model="glm-5.1:cloud",
)

# 2. Tools — the upstream's minimal set: read, write, edit, bash.
tools = [
    ReadFileTool(),
    WriteFileTool(),
    EditFileTool(),
    BashTool(),
]

# 3. Prompt — terse, root-cause focused, tests-as-oracle.
prompt = chimera.Prompt.from_string(
    "You are a coding agent. Goal: make failing tests pass.\n"
    "Loop: read failing test -> read source -> edit ONE file -> "
    "run tests via bash -> stop when tests pass.\n"
    "Be minimal. Don't add unrelated changes."
)

# 4. Loop — ReAct. yolo_mode lets the agent run autonomously
#    (no permission prompts on bash / edit_file).
loop = chimera.ReAct(max_steps=8, config=LoopConfig(yolo_mode=True))

# 5. Environment — local filesystem rooted at the current directory.
env = chimera.LocalEnvironment(".")
env.setup()

# Assemble.
agent = chimera.Agent(
    provider=provider,
    tools=tools,
    loop=loop,
    prompt=prompt,
    name="swe-agent-clone",
)
```

That's the entire agent: 18 lines after the imports, well under the 60-line headline.

## 3. Run it

```python
result = agent.run(
    "Fix the bug in src/auth.py so tests/test_auth.py passes.",
    env=env,
)
env.cleanup()

print(f"Steps:   {result.steps}")
print(f"Success: {result.success}")
print(f"Cost:    ${result.cost:.6f}")
print(f"Output:  {result.output}")
```

If you want to watch the agent reason step-by-step, swap `agent.run(...)` for `agent.iter_steps(...)`:

```python
gen = agent.iter_steps(
    "Fix the bug in src/auth.py so tests/test_auth.py passes.",
    env=env,
)
try:
    while True:
        step = next(gen)
        for tc in step.tool_calls:
            print(f"  -> {tc.name}({tc.arguments})")
        for tr in step.tool_results:
            print(f"  <- {tr.output[:80]}")
except StopIteration as final:
    result = final.value
```

## 4. Real example

Set up a tiny repo with a bug and a failing test:

```text
src/auth.py
└── def add(a, b):
        # Bug: returns a - b instead of a + b
        return a - b

tests/test_auth.py
└── from auth import add
    def test_add():
        assert add(2, 3) == 5
        assert add(0, 0) == 0
        assert add(-1, 1) == 0
    if __name__ == "__main__":
        test_add()
        print("PASS")
```

Run the agent against it:

```bash
export OLLAMA_API_KEY="..."   # ollama.com key
python build_swe_agent.py
```

The agent reads both files, locates the off-by-one bug, applies a one-line edit, runs the test, sees `PASS`, and stops. End-to-end in well under 10 seconds.

## 5. Or use the preset

The same posture is shipped as a `CodingAgent` preset:

```python
from chimera.assembly.coding_agent import CodingAgent

agent = CodingAgent.from_preset("swebench", model="glm-5.1:cloud")
```

The preset adds production niceties (transcripts off, compaction off, streaming off, permissions off, `max_turns=30`) tuned for batch evaluation. Use it for benchmark runs; use the hand-built version above when you want to understand every block.

## Full code

The complete script — imports + 5 blocks + run — fits in 71 lines including comments and blank lines:

```python
"""Recreate a SWE-Agent-style coding agent in ~60 lines using chimera.

Posture: minimal tool set (read/write/edit/bash), root-cause focus,
ReAct loop, "stop when tests pass" convergence.
"""
from __future__ import annotations

import os

# 1. Point at any provider. Here: Ollama Cloud's glm/gpt-oss models.
os.environ.setdefault("OLLAMA_HOST", "https://ollama.com")

import chimera
from chimera.core.loop_config import LoopConfig
from chimera.tools.bash import BashTool
from chimera.tools.edit import EditFileTool
from chimera.tools.read import ReadFileTool
from chimera.tools.write import WriteFileTool

# 2. Provider — any chimera-supported LLM with tool calling.
#    `glm-5.1:cloud` and `gpt-oss:120b-cloud` both work on Ollama Cloud.
provider = chimera.create_provider(
    provider_type="ollama",
    model="glm-5.1:cloud",
)

# 3. Tools — the upstream's minimal set.
tools = [
    ReadFileTool(),
    WriteFileTool(),
    EditFileTool(),
    BashTool(),
]

# 4. System prompt — terse, root-cause focused, test-driven convergence.
prompt = chimera.Prompt.from_string(
    "You are a coding agent. Goal: make failing tests pass.\n"
    "Loop: read failing test -> read source -> edit ONE file -> "
    "run tests via bash -> stop when tests pass.\n"
    "Be minimal. Don't add unrelated changes."
)

# 5. Loop — ReAct with yolo_mode (skip the interactive permission gate).
loop = chimera.ReAct(max_steps=8, config=LoopConfig(yolo_mode=True))

# 6. Environment — sandboxed working directory.
env = chimera.LocalEnvironment(".")
env.setup()

# Assemble: provider + tools + loop + prompt = Agent.
agent = chimera.Agent(
    provider=provider,
    tools=tools,
    loop=loop,
    prompt=prompt,
    name="swe-agent-clone",
)

# Run.
result = agent.run(
    "Fix the bug in src/auth.py so tests/test_auth.py passes.",
    env=env,
)
env.cleanup()

print(f"Steps:   {result.steps}")
print(f"Success: {result.success}")
print(f"Cost:    ${result.cost:.6f}")
print(f"Output:  {result.output}")
```

## Verified output

Run against Ollama Cloud (`OLLAMA_HOST=https://ollama.com`, model `glm-5.1:cloud`) with the demo repo described above. This is real captured output, not pseudo-code:

```text
[step 1]
  -> read_file({'path': 'tests/test_auth.py'})
  -> read_file({'path': 'src/auth.py'})
  <- import sys, os sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
  <- def add(a, b):     # Bug: returns a - b instead of a + b     return a - b
[step 2]
  -> edit_file({'path': 'src/auth.py', 'old_string': '    return a - b', 'new_string': '    ...)
  <- Edited src/auth.py
[step 3]
  -> bash({'command': 'cd /home/user && python tests/test_auth.py'})
  <-  STDERR: /bin/sh: line 0: cd: /home/user: No such file or directory
[step 4]
  -> bash({'command': 'python tests/test_auth.py'})
  <- PASS
[step 5]
  say: The bug was that `add` returned `a - b` instead of `a + b`.
       Fixed by changing the return statement to `return a + b`. Tests now pass.

success=True  steps=5  cost=$0.008054  elapsed=7.8s
```

Five LLM turns, one bad shell command (the agent self-corrects on step 4), one successful edit, one passing test. $0.008 of inference, 7.8 seconds wall clock.

## Where to next

- [Build Aider in 50 lines](./build-aider-in-50-lines.md) — diff-first, conservative posture.
- [Build Cline in 70 lines](./build-cline-in-70-lines.md) — plan-then-act two-phase pipeline.
- [Build Your Own Coding Agent](./build-your-own-coding-agent.md) — the longer walkthrough with REPL, cost tracking, undo.
- [Architecture](../architecture.md) — the 8-phase stack these primitives live in.
