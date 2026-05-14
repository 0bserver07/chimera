---
title: Build Aider in 50 Lines
description: Tutorial — recreate an Aider-style coding agent (diff-first, conservative tool use) from chimera primitives. Includes verified output from a real Ollama Cloud run.
---

# Build Aider in 50 Lines

The upstream we're modelling here is Aider: a coding agent with a deliberately conservative posture. It doesn't go scanning the repo for context; it only reads the files you explicitly hand it. Its edits are precise — it leans on diff-style replacements rather than full-file rewrites — and the tool surface is small.

Where SWE-Agent runs until the tests pass, this agent runs until the diff applies and the user's stated verification passes. The vibe is "surgical assistant," not "autonomous engineer."

## What changes vs. SWE-Agent

Three swaps:

1. **No `search`, no `list_files`, no `repo_map`.** The tool list is smaller. The agent can't go fishing for context — it has to ask, or work with what's named in the task.
2. **`replace_in_file` instead of `edit_file`.** `replace_in_file` does pattern-based diffs with counts; it complains when ambiguous. Closer to a unified-diff workflow than a single-string edit.
3. **Tighter `max_steps`.** Conservative agents shouldn't loop. We cap at 6.

Everything else (provider, environment, ReAct loop) is the same as SWE-Agent.

## Full code

```python
"""Recreate an Aider-style coding agent in ~50 lines.

Posture: diff-first edits, no eager file scanning, conservative tool use.
The agent only reads files it has been told about and writes via precise
string-replace diffs.
"""
from __future__ import annotations

import os

os.environ.setdefault("OLLAMA_HOST", "https://ollama.com")

import chimera
from chimera.core.loop_config import LoopConfig
from chimera.tools.bash import BashTool
from chimera.tools.read import ReadFileTool
from chimera.tools.replace_in_file import ReplaceInFileTool

# Provider — Ollama Cloud's glm-5.1:cloud.
provider = chimera.create_provider(
    provider_type="ollama",
    model="glm-5.1:cloud",
)

# Conservative tool set — read, precise replace, bash for verification.
# No `search`, no `list_files`, no `repo_map`. The agent edits files the
# user explicitly mentions, nothing more.
tools = [
    ReadFileTool(),
    ReplaceInFileTool(),
    BashTool(),
]

prompt = chimera.Prompt.from_string(
    "You are a diff-first coding agent.\n"
    "Rules:\n"
    "  - Only read files the user explicitly mentions.\n"
    "  - Make edits via precise string replacement (replace_in_file).\n"
    "  - Each edit must change as little as possible.\n"
    "  - After editing, run the tests via bash and stop on success.\n"
)

loop = chimera.ReAct(max_steps=6, config=LoopConfig(yolo_mode=True))
env = chimera.LocalEnvironment(".")
env.setup()

agent = chimera.Agent(
    provider=provider,
    tools=tools,
    loop=loop,
    prompt=prompt,
    name="aider-clone",
)

result = agent.run(
    "Edit src/auth.py to fix the bug. "
    "Verify by running `python tests/test_auth.py`.",
    env=env,
)
env.cleanup()
print(f"steps={result.steps} success={result.success} cost=${result.cost:.6f}")
print(f"output: {result.output}")
```

50 lines of agent code (62 lines of file including imports, docstring, comments, blanks).

## Verified output

Run against Ollama Cloud (`OLLAMA_HOST=https://ollama.com`, `OLLAMA_API_KEY=...`, model `glm-5.1:cloud`) with the same buggy `src/auth.py` repo as the SWE-Agent tutorial. Real captured output:

```text
[step 1]
  -> read_file({'path': 'src/auth.py'})
  -> read_file({'path': 'tests/test_auth.py'})
  <- def add(a, b):     # Bug: returns a - b instead of a + b     return a - b
  <- import sys, os sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
[step 2]
  -> replace_in_file({'path': 'src/auth.py', 'pattern': 'return a - b', 'replacement': 'return a +...)
  <- 1 replacement(s) made in src/auth.py
[step 3]
  -> bash({'command': 'python tests/test_auth.py'})
  <- PASS
[step 4]
  say: The bug was that `add` returned `a - b` instead of `a + b`.
       Fixed by changing the return statement, and the test now passes.

success=True steps=4 cost=$0.005488 elapsed=8.8s
```

Four turns, one diff, one test run. $0.005 of inference. The agent never scanned the project — it only touched the two files named in the task.

## When to pick this posture

- **You want predictable, small diffs.** Big sweeping refactors are an anti-pattern here; the tools punish ambiguity.
- **You're in an interactive review loop.** Pair this agent with `chimera.review.ReviewOrchestrator` or a human gating each edit.
- **You're worried about the agent over-exploring.** Strip out `search` and the agent literally cannot wander.

## Where to next

- [Build SWE-Agent in 60 lines](/chimera/tutorials/build-swe-agent-in-60-lines/) — autonomous, test-driven posture.
- [Build Cline in 70 lines](/chimera/tutorials/build-cline-in-70-lines/) — plan-first two-phase pipeline.
- [Build Your Own Coding Agent](/chimera/tutorials/build-your-own-coding-agent/) — the longer walkthrough.
