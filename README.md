# Chimera

AI that reads, writes, and debugs code — tools like Claude Code and Codex do this. Chimera is a Python library for building these tools yourself, and a plugin that makes Claude Code better.

**Status: Alpha** — 3953 tests. Benchmarked with GLM-5: HumanEval 90.9% pass@1 (164 problems), SWE-bench Lite 10% (2/20), Terminal-Bench 30%.

## Who This Is For

**You build with CLI coding agents.**
You use Claude Code, Codex, OpenCode, Amp, or Mono daily. You know what it feels like when an agent reads your codebase, edits files, and runs tests from your terminal. You want to build your own — with your model, your tools, your rules — or take apart how these agents work to understand why they behave differently.

**You're curious about coding agents.**
You've seen demos of AI writing entire apps. You want to understand what's actually happening — what the pieces are, how the loop works, why some agents are better at certain tasks. Chimera breaks it all down into parts you can inspect, modify, and run yourself.

## What It Does

A coding agent is an LLM connected to your filesystem. It reads code, decides what to change, edits files, runs tests, and repeats until the task is done. Claude Code and Codex are coding agents.

Chimera gives you two things:

1. **A plugin for Claude Code** that adds codebase search, auto-testing, code review, and context management — capabilities Claude Code doesn't have out of the box.

2. **A Python library** for building your own coding agents from modular pieces — pick your LLM, pick your tools, pick your strategy, wire them together.

## Install

```bash
pip install chimera-run[anthropic]   # Claude
pip install chimera-run[openai]      # GPT
pip install chimera-run[google]      # Gemini
pip install chimera-run[all]         # everything
```

Requires Python 3.11+.

## Build Your Own Coding Agent

```python
from chimera.assembly.coding_agent import CodingAgent

# One line — full-featured coding agent with 20 tools
agent = CodingAgent(model="claude-sonnet-4-20250514")

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
| `claude_code` | 20 (bash, read, write, edit, search, git, test, agent, skill, ...) | Permissions, hooks, transcripts, compaction, streaming |
| `codex` | 20 | Permissions, transcripts (no hooks) |
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

## Use It With Claude Code

Install the plugin to get immediate improvements. No Python code to write.

**Hooks** run automatically on every edit:
- Path validation — blocks edits to files that don't exist (no more hallucinated paths)
- Auto-test — finds and runs related tests after every file change
- Auto-lint — runs your linter after every edit
- Security scan — blocks dangerous bash commands
- Verify done — runs the full test suite before Claude can declare "done"

**MCP servers** give Claude new tools to call:
- `chimera-search` — semantic codebase search + symbol lookup
- `chimera-review` — multi-perspective code review (logic, security, tests, architecture, and 4 more)
- `chimera-testgen` — generate test skeletons from source analysis
- `chimera-migration` — scan for and apply code migrations (Python 2 to 3, CJS to ESM)

[Setup guide](docs/playbooks/00-quick-start.md) — install in 2 minutes.

## How It's Organized

Chimera is an 8-layer stack. Each layer works independently — use just what you need.

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
                    repeat. 20 built-in tools (read, write, edit, bash, search,
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

| Benchmark | GLM-5 | GLM-5.1 |
|-----------|-------|---------|
| HumanEval (164 problems) | 90.9% pass@1 | 66.5% pass@1 |
| SWE-bench Lite (20 instances) | 10% | 10% (2/20) |
| Terminal-Bench (10 tasks) | 30% | — |

[Full transparency report](docs/benchmarks/README.md) with 13 tracked issues.

## When Chimera, When Claude Code?

**Use Claude Code** if you want a polished product that works today.

**Use Chimera** if you want to:
- Make Claude Code better — add search, auto-test, review, context management via the plugin
- Build your own coding agent — different LLM, different tools, different strategy
- Understand how coding agents work — every major architecture decomposed into swappable pieces
- Research and benchmark — compare agent architectures with controlled experiments

## Links

- [Quick Start: Claude Code Plugin](docs/playbooks/00-quick-start.md) — hooks, MCP servers, skills
- [Build Your Own Agent](docs/playbooks/08-building-agents.md) — full library guide
- [All Playbooks](docs/playbooks/) — 13 guides covering every feature
- [Examples](examples/) — 46 runnable scripts
- [Function Synthesis](docs/function-synthesis.md) — compile specs into callable `.chi` bundles
  - 3 runtime backends (llama.cpp, transformers, ONNX), schema validation, streaming invoke
  - `LocalCompiler` for real PEFT fine-tuning; publish and fetch bundles via `chimera fs push | pull` (Hugging Face Hub + S3)
  - 10 CLI sub-verbs: `compile`, `run`, `list`, `rm`, `info`, `push`, `pull`, `import-peft`, `login`, `rename`
- [Benchmarks](docs/benchmarks/README.md) — transparency framework
- [Contributing](CONTRIBUTING.md) — setup, workflow, code style
- [Changelog](CHANGELOG.md) — version history

## License

[MIT](LICENSE)
