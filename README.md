# Chimera

AI that reads, writes, and debugs code — tools like Claude Code and Codex do this. Chimera is a Python library for building these tools yourself, and a plugin that makes Claude Code better.

**Status: Alpha** — 2774 tests, benchmarked on HumanEval (90.9%) and SWE-bench.

## What It Does

A coding agent is an LLM connected to your filesystem. It reads code, decides what to change, edits files, runs tests, and repeats until the task is done. Claude Code and Codex are coding agents.

Chimera gives you two things:

1. **A plugin for Claude Code** that adds codebase search, auto-testing, code review, and context management — capabilities Claude Code doesn't have out of the box.

2. **A Python library** for building your own coding agents from modular pieces — pick your LLM, pick your tools, pick your strategy, wire them together.

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

## Build Your Own Agent

```python
import chimera

provider = chimera.create_provider()  # Anthropic, OpenAI, Google, Ollama, or any compatible API
agent = chimera.Agent(provider=provider, tools=list(chimera.AGENT_TOOLS))
result = agent.run("Fix the failing test in auth.py", env=chimera.LocalEnvironment("."))
```

Swap any piece:

```python
# Different loop strategy
agent = chimera.Agent(provider, loop=chimera.PlanAndExecute())   # plan first, then act
agent = chimera.Agent(provider, loop=chimera.Reflexion())        # self-critique after each attempt
agent = chimera.Agent(provider, loop=chimera.TreeOfThought())    # explore multiple approaches

# Replicate existing agents
agent = chimera.AgentPreset.SWE_AGENT.build(provider)   # SWE-Agent's retry loop
agent = chimera.AgentPreset.AIDER.build(provider)        # Aider's lint feedback loop
agent = chimera.AgentPreset.CLINE.build(provider)        # Cline's plan-then-act
agent = chimera.AgentPreset.CODEX.build(provider)        # Codex CLI's full tool suite

# Iterate with real-time streaming (for building UIs)
for step in agent.iter_steps("Fix the bug", env):
    print(step.message.content)
    if step.pending_approval:
        step.pending_approval.approve()  # interactive permission system
```

[Full library guide](docs/playbooks/08-building-agents.md) — providers, tools, loops, sessions, streaming, permissions, composition.

## How It's Organized

Chimera is an 8-layer stack. Each layer works independently — use just what you need.

```
What you run        CLI commands: chimera code / synthesize / eval / review / ci-fix
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

| Benchmark | Score | Model |
|-----------|-------|-------|
| HumanEval (164 problems) | **90.9% pass@1** | GLM-5 |
| Terminal-Bench (10 tasks) | **30%** | GLM-5 |
| SWE-bench Lite (20 instances) | **10%** | GLM-5 |

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
- [Examples](examples/) — 39 runnable scripts
- [Benchmarks](docs/benchmarks/README.md) — transparency framework
- [Contributing](CONTRIBUTING.md) — setup, workflow, code style
- [Changelog](CHANGELOG.md) — version history

## License

[MIT](LICENSE)
