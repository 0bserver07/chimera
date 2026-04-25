# Chimera

AI that reads, writes, edits, and iterates on code with tests. Chimera is a Python library for building these tools yourself, plus a ready-to-run coding agent on top of it.

**Status: Alpha** — 3922 passing tests. Reproducible benchmarks with GLM-5.1: HumanEval 66.5% pass@1 (109/164), SWE-bench Lite 10% (2/20, top-20 smallest patches). Raw results in `data/`.

## Who This Is For

**You build with CLI coding agents.**
You use terminal-native AI tools daily and you know what it feels like when an agent reads your codebase, edits files, and runs tests from your shell. You want to build your own — with your model, your tools, your rules — or take apart how these agents work to understand why they behave differently.

**You're curious about coding agents.**
You've seen demos of AI writing entire apps. You want to understand what's actually happening — what the pieces are, how the loop works, why some agents are better at certain tasks. Chimera breaks it all down into parts you can inspect, modify, and run yourself.

## What It Does

A coding agent is an LLM connected to your filesystem. It reads code, decides what to change, edits files, runs tests, and repeats until the task is done.

Chimera gives you two things:

1. **A coding-agent harness with a plugin system** — codebase search, auto-testing, code review, and context management, exposed as hooks, MCP servers, and skills you can wire into any compatible host.

2. **A Python library** for building your own coding agents from modular pieces — pick your LLM, pick your tools, pick your strategy, wire them together.

## Install

Latest release: **v0.3.0** ([release notes](https://github.com/0bserver07/chimera/releases/tag/v0.3.0)).

Not yet on PyPI. Install from source:

```bash
pip install "git+https://github.com/0bserver07/chimera.git@v0.3.0#egg=chimera-run[anthropic]"   # GLM-5 / Anthropic-compatible
pip install "git+https://github.com/0bserver07/chimera.git@v0.3.0#egg=chimera-run[openai]"      # GPT
pip install "git+https://github.com/0bserver07/chimera.git@v0.3.0#egg=chimera-run[all]"         # anthropic + openai + browser + remote
```

Requires Python 3.11+. A `chimera-run` PyPI release is planned post-alpha.

## Build Your Own Coding Agent

```python
from chimera.assembly.coding_agent import CodingAgent

# One line — full-featured coding agent with 24 tools.
# Requires ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic and ANTHROPIC_AUTH_TOKEN in env.
agent = CodingAgent(model="glm-5")

# Run a task
import asyncio

async def main():
    async for event in agent.run("Fix the bug in auth.py"):
        print(event.type.value, getattr(event.data, 'content', '')[:100])

asyncio.run(main())
```

### Presets

| Preset | Tools | Features |
|--------|-------|----------|
| `coding_agent` | 24 (bash, read, write, edit, search, git, test, agent, skill, ...) | Permissions, hooks, transcripts, compaction, streaming |
| `codex` | 24 | Permissions, transcripts (no hooks) |
| `minimal` | 4 (bash, read, write, edit) | No extras |
| `explore` | 3 (read, search, list) | Read-only |

```python
# Codex-style agent
agent = CodingAgent.from_preset("codex", model="gpt-4o")

# Minimal agent for simple tasks
agent = CodingAgent.from_preset("minimal", model="claude-haiku-3.5")

# Custom API endpoint (any Anthropic-compatible API)
import os
os.environ["ANTHROPIC_BASE_URL"] = "https://your-api.com/v1"
os.environ["ANTHROPIC_AUTH_TOKEN"] = "your-key"
agent = CodingAgent(model="your-model")
```

### Architecture

Chimera is modular — every component is replaceable:

```
CodingAgent
├── Provider (Anthropic, OpenAI, Google, Ollama, or any compatible API)
├── Tools (20+ built-in, plus custom tools, MCP servers, skills)
├── AgentLoop (async generator with streaming, error recovery, abort)
├── Permissions (multi-source rules, 6 modes, interactive prompts)
├── Hooks (27 lifecycle events, shell/LLM/function hooks)
├── Commands (slash commands, skills from .chimera/skills/)
├── Sub-Agents (3-tier context isolation, background tasks)
├── State (content replacement, file cache, session transcripts)
└── Infrastructure (feature flags, analytics, memory, compaction)
```

See [Architecture](https://0bserver07.github.io/chimera/architecture/) for the full module map.

## Run It Standalone

The Mink CLI ships a fully assembled coding agent with no extra setup:

```bash
chimera mink                                    # interactive REPL on Ollama Kimi K2.6 by default
chimera mink -p "summarize this repo"           # one-shot, prints to stdout
chimera mink runs list                          # inspect every persisted run
chimera mink agents list                        # show available agent presets
chimera code                                    # legacy stack with slash commands and session save
```

`chimera mink` is the v0.3.0 coding REPL: streaming tool calls, hooks,
permissions from `.claude/settings.json`, MCP, subagents, and a rich
TUI on a TTY (auto-disabled when piping; force off with `--no-color`).
See the [Mink quickstart](docs/mink/quickstart.md) for the walking
skeleton, env vars, and the runs/agents subcommand surface, and
[`docs/mink/providers.md`](docs/mink/providers.md) for the full
provider matrix (Ollama, Anthropic, OpenAI, Google, OpenAI-compat).

**Hooks** run automatically on every edit:
- Path validation — blocks edits to files that don't exist (no more hallucinated paths)
- Auto-test — finds and runs related tests after every file change
- Auto-lint — runs your linter after every edit
- Security scan — blocks dangerous bash commands
- Verify done — runs the full test suite before the agent can declare "done"

**MCP servers** give the agent new tools to call:
- `chimera-search` — semantic codebase search + symbol lookup
- `chimera-review` — multi-perspective code review (logic, security, tests, architecture, and 4 more)
- `chimera-testgen` — generate test skeletons from source analysis
- `chimera-migration` — scan for and apply code migrations (Python 2 to 3, CJS to ESM)

The plugin honors a `settings.json` schema for ecosystem interop, so the same hooks/MCP/skills also drop into any host that follows that convention.

[Setup guide](docs/playbooks/00-quick-start.md) — install in 2 minutes.

## How It's Organized

Chimera is an 8-layer stack. Each layer has a documented API boundary; swap any provider, tool, env, or strategy without touching the rest.

```
What you run        CLI commands: chimera code / synthesize / eval / review / ci-fix / fs
                    ─────────────────────────────────────────────────────────────────
Automated           CI repair, code review, research, migration planning, doc and
workflows           test generation — multi-step pipelines built on the agent layer
                    ─────────────────────────────────────────────────────────────────
Iterating on code   Give it a spec and tests, it keeps trying until the tests pass.
                    Strategies: converge on tests, search a tree of approaches,
                    generate-then-verify (CEGIS), curriculum learning
                    ─────────────────────────────────────────────────────────────────
Measuring quality   Run benchmarks (HumanEval, SWE-bench, AIMO, custom), collect
                    pass rates and costs, compare agent configurations
                    ─────────────────────────────────────────────────────────────────
The agent itself    An LLM in a loop: think, call a tool, observe the result,
                    repeat. 24 built-in tools (read, write, edit, bash, search,
                    git, test, web fetch, etc). 4 loop strategies.
                    ─────────────────────────────────────────────────────────────────
LLM providers       Anthropic, OpenAI, Google, Ollama, Modal, or any
                    OpenAI-compatible API. Streaming, async, cost tracking.
                    ─────────────────────────────────────────────────────────────────
Plumbing            Auth, sessions (save/resume/fork), event bus, permissions,
                    context compaction, secrets, plugins, MCP, LSP
                    ─────────────────────────────────────────────────────────────────
Where code runs     Your filesystem, a Docker container, a git branch,
                    a remote server, or a cloud sandbox
```

## Benchmarks

Reproducible runs with raw data in `data/`:

| Benchmark | GLM-5.1 | Raw data |
|-----------|---------|----------|
| HumanEval (164 problems) | 66.5% pass@1 (109/164) | `data/humaneval-glm51-results.json` |
| SWE-bench Lite (20 smallest patches) | 10% (2/20) | `data/swebench-lite-glm51-results.jsonl` |

Earlier GLM-5 runs (HumanEval, Terminal-Bench) exist in our notes but the raw result files were not preserved; we won't publish unverifiable numbers. [Full transparency report](docs/benchmarks/README.md) — every benchmark has a status, methodology, and known gaps.

## Run It Free with Ollama

Chimera speaks Ollama's Anthropic-compatible API out of the box. You can run the full agent against `kimi-k2.6:cloud`, `glm-5.1:cloud`, or any local Qwen/Llama with zero code changes:

```bash
export ANTHROPIC_BASE_URL=http://localhost:11434
export ANTHROPIC_AUTH_TOKEN=ollama
python examples/agent/ollama_coding_agent.py --model kimi-k2.6:cloud
```

[Full Ollama setup guide](https://0bserver07.github.io/chimera/guides/use-with-ollama/) — prerequisites, recommended models, context window notes, troubleshooting.

## When to Reach for Chimera

Use Chimera if you want to:
- Run a coding agent on your own model (local Ollama, GLM, GPT, Anthropic-compatible) with hooks, MCP, and skills wired in
- Build your own coding agent — different LLM, different tools, different strategy
- Understand how coding agents work — every major architecture decomposed into swappable pieces
- Research and benchmark — compare agent architectures with controlled experiments

## Links

- [Quick Start](docs/playbooks/00-quick-start.md) — hooks, MCP servers, skills
- [Mink Quickstart](docs/mink/quickstart.md) — `chimera mink` REPL, runs/agents subcommands
- [Mink Providers](docs/mink/providers.md) — backend matrix, env vars, troubleshooting
- [Build Your Own Agent](docs/playbooks/08-building-agents.md) — full library guide
- [All Playbooks](docs/playbooks/) — 13 guides covering every feature
- [Examples](examples/) — 28 curated runnable scripts across 7 categories
- [Function Synthesis](docs/function-synthesis.md) — compile specs into callable `.chi` bundles
  - 3 runtime backends (llama.cpp, transformers, ONNX), schema validation, streaming invoke
  - `LocalCompiler` for real PEFT fine-tuning; publish and fetch bundles via `chimera fs push | pull` (Hugging Face Hub + S3)
  - 10 CLI sub-verbs: `compile`, `run`, `list`, `rm`, `info`, `push`, `pull`, `import-peft`, `login`, `rename`
- [Benchmarks](docs/benchmarks/README.md) — transparency framework
- [Benchmark adapters](docs/mink/benchmarks.md) — every adapter under `chimera/eval/benchmarks/`, status, and how to run
- [Contributing](CONTRIBUTING.md) — setup, workflow, code style
- [Changelog](CHANGELOG.md) — version history

## License

[MIT](LICENSE)
