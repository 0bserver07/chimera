---
title: "CLI & REPL"
description: "CLI & REPL"
---

Chimera's command-line interface provides 11 subcommands for code synthesis, evaluation, interactive coding, code review, CI fixing, and more. The `chimera code` subcommand launches an interactive REPL with 16 slash commands for session management, debugging, and agent control.

## Quick Start

```bash
# Synthesize code from a specification
chimera synthesize --spec "Build a REST API with FastAPI" --tests ./tests/

# Launch the interactive coding REPL
chimera code --model claude-sonnet-4-20250514

# Run AI code review on a diff
chimera review --diff changes.patch

# Fix CI failures automatically
chimera ci-fix --log ci-output.log
```

## Subcommands

| Command | Description | Key flags |
|---------|-------------|-----------|
| `chimera synthesize` | Synthesize code from spec + tests | `--spec`, `--tests`, `--model`, `--strategy`, `--max-iterations`, `--max-cost`, `--patience`, `--output`, `--provider` |
| `chimera synth` | Alias for `synthesize` | (same as above) |
| `chimera eval` | Evaluate against benchmarks | `--benchmark` (required), `--dataset`, `--limit`, `--model`, `--output` |
| `chimera bench` | Run benchmark suites | `--suite` (required), `--tasks-dir`, `--model`, `--output` |
| `chimera code` | Interactive REPL | `--model`, `--workdir`, `--max-steps`, `--mode`, `--models` |
| `chimera review` | AI code review | `--diff` (required), `--model`, `--max-rounds` |
| `chimera ci-fix` | Diagnose/fix CI failures | `--log` (required), `--model`, `--max-attempts` |
| `chimera research` | Research a question | `--question` (required), `--model`, `--workdir` |
| `chimera docs` | Generate API documentation | `--source` (required), `--output` |
| `chimera testgen` | Generate test skeletons | `--source` (required), `--output` |
| `chimera migrate` | Apply migration presets | `--source` (required), `--preset` (required) |
| `chimera plugins` | Manage plugins | positional: `action` (search/install/uninstall), `query` |

## Usage

### Code synthesis

Synthesize code that passes a test suite:

```bash
# From a spec string
chimera synthesize --spec "Implement a binary search tree" --tests ./tests/ --max-iterations 30

# With cost budget
chimera synthesize --spec spec.txt --tests ./tests/ --max-cost 5.00 --strategy convergence

# Using a different provider/model
chimera synthesize --spec "Calculator CLI" --model gpt-4o --provider openai
```

### Evaluation and benchmarks

Run agents against standard benchmarks:

```bash
# Evaluate on SWE-bench
chimera eval --benchmark swe-bench --dataset ./swe-bench-lite.json --limit 50 --output results.json

# Evaluate on HumanEval
chimera eval --benchmark human-eval --model claude-sonnet-4-20250514

# Run a custom benchmark suite
chimera bench --suite custom --tasks-dir ./my-tasks/ --output bench-results.json
```

Available benchmarks: `swe-bench`, `human-eval`, `aimo`, `custom`.

### Code review

Run an AI reviewer/author iteration loop on a diff:

```bash
chimera review --diff feature-branch.patch --max-rounds 3 --model claude-sonnet-4-20250514
```

### CI fix

Parse CI logs, diagnose failures, and apply fixes:

```bash
chimera ci-fix --log ./ci-output.log --max-attempts 3
```

### Research

Decompose a question into sub-tasks, research each, and synthesize findings:

```bash
chimera research --question "What are the performance tradeoffs of B-trees vs LSM trees?" --workdir ./notes/
```

### Documentation and test generation

```bash
# Generate API docs from source
chimera docs --source ./src/ --output ./docs/api/

# Generate test skeletons
chimera testgen --source ./src/ --output ./tests/generated/
```

### Migrations

Apply rule-based code transformations:

```bash
chimera migrate --source ./src/ --preset python2-to-3
chimera migrate --source ./src/ --preset commonjs-to-esm
```

### Plugin management

```bash
chimera plugins search "code formatter"
chimera plugins install chimera-prettier
chimera plugins uninstall chimera-prettier
```

## Interactive REPL

Launch with `chimera code`:

```bash
chimera code --model claude-sonnet-4-20250514 --workdir ./myproject --max-steps 50

# Cycle through multiple models automatically
chimera code --models glm-5,claude-sonnet-4-20250514,gpt-4o

# Use RPC or JSON output mode for programmatic consumers
chimera code --mode rpc
chimera code --mode json
```

The REPL loads `AGENT_TOOLS` -- a 13-tool preset that extends `DEFAULT_TOOLS` with edit, search, list_files, test, git, replace_in_file, repo_map, think, and todo -- plus `AskUserTool` for interactive prompts. It also auto-discovers project context from `chimera.yaml`/`.chimera/`, and loads MCP servers from `~/.chimera/mcp.json` if present.

### Terminal modes and mid-turn interaction

The REPL operates in two terminal modes automatically:

- **Readline idle mode** — used when waiting for user input; supports history and line editing.
- **Raw stdin mode** — active while the agent runs; captures keystrokes without blocking the loop.

While the agent is running you can:
- **Type a message** — it is queued and delivered as a steering message to the running turn.
- **Press Ctrl+C** — cancels the current turn via `CancellationToken`.

Sessions are auto-saved to `~/.chimera/sessions/` after every turn so progress is never lost.

### `--mode` flag

| Value | Description |
|-------|-------------|
| `interactive` | Default terminal REPL with readline |
| `rpc` | JSON-RPC 2.0 over stdio; suitable for IDE extensions |
| `json` | One JSON object per line (newline-delimited) |

### `--models` flag

Accepts a comma-separated list of model names.  The REPL cycles through them
in order using `/model next` / `/model prev`, or automatically based on cost
or failure policies.

### REPL Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | List all available slash commands |
| `/model` | Show the current model name |
| `/cost` | Display total cost and per-model breakdown |
| `/clear` | Clear conversation context |
| `/history` | Show last 10 messages (role + content preview) |
| `/tools` | List loaded tools with descriptions |
| `/context` | Show message count and estimated token usage |
| `/debug` | Toggle debug mode on/off |
| `/session save [name]` | Save the current session |
| `/session fork` | Fork the current session |
| `/session list` | Show session management help |
| `/compact` | Compact conversation context to reduce token usage |
| `/audit` | Show audit log summary (tool call decisions) |
| `/audit clear` | Clear the audit log |
| `/checkpoint save [name]` | Create a named checkpoint |
| `/checkpoint list` | List all checkpoints |
| `/checkpoint restore <name>` | Restore to a named checkpoint |
| `/checkpoint undo` | Undo to the most recent checkpoint |
| `/agent list` | List available agent presets |
| `/init` | Re-initialize the agent with updated project context |
| `/yolo` | Toggle YOLO mode (auto-approve all tool calls) |
| `/tree` | Display the session branch tree |
| `/branch [name]` | Create a new named branch from the current session |
| `/switch <name>` | Switch to an existing branch by name |
| `/model next` | Cycle to the next model in the `--models` list |
| `/model prev` | Cycle to the previous model in the `--models` list |
| `/exit` or `/quit` | Exit the REPL |

### REPL example session

```
$ chimera code --workdir ./myproject
chimera code v0.1.0 | model: claude-sonnet-4-20250514 | /help for commands

> Read the main.py file and explain what it does

  [The agent reads the file and provides an explanation]

  [cost: $0.0042 | steps: 2]

> /cost
Total cost: $0.0042
Breakdown:
  claude-sonnet-4-20250514: $0.0042

> /tools
  read: Read file contents from the filesystem
  write: Write content to a file
  edit: Edit a file with search/replace
  bash: Execute a shell command
  ...

> /checkpoint save before-refactor

> Refactor the database module to use async/await

  [cost: $0.0156 | steps: 8]

> /checkpoint undo
Undone to checkpoint: before-refactor

> /exit
Bye!

Total cost: $0.0198
```

## Key Functions

| Function | Module | Description |
|----------|--------|-------------|
| `build_parser()` | `chimera.cli.main` | Build the top-level `argparse.ArgumentParser` with all subcommands |
| `main(argv)` | `chimera.cli.main` | CLI entry point; parses args and dispatches to subcommand handlers |
| `run_code(args)` | `chimera.cli.code` | Run the interactive coding REPL |
| `run_synthesize(args)` | `chimera.cli.main` | Execute the synthesize command |
| `run_eval(args)` | `chimera.cli.main` | Execute the eval command |
| `run_bench(args)` | `chimera.cli.main` | Execute the bench command |
| `run_review(args)` | `chimera.cli.main` | Execute the review command |
| `run_ci_fix(args)` | `chimera.cli.main` | Execute the ci-fix command |
| `run_research(args)` | `chimera.cli.main` | Execute the research command |
| `run_docs(args)` | `chimera.cli.main` | Execute the docs command |
| `run_testgen(args)` | `chimera.cli.main` | Execute the testgen command |
| `run_migrate(args)` | `chimera.cli.main` | Execute the migrate command |
| `run_plugins(args)` | `chimera.cli.main` | Execute the plugins command |

## Integration

- **Entry point**: The CLI is registered as a console script entry point (`chimera`), backed by `chimera.cli.main:main`.
- **Provider auto-detection**: All subcommands that accept `--model` use `create_provider()` from `chimera.providers.factory` to auto-detect the provider from the model name.
- **REPL components**: The REPL wires together `Agent`, `Session`, `ReAct` loop, `LoopConfig`, `CostTracker`, `ConsoleStreamHandler`, and `LocalEnvironment`.
- **MCP integration**: The REPL auto-loads MCP servers from `~/.chimera/mcp.json` using `MCPToolSource.from_config()`.
- **Project context**: The REPL auto-discovers project configuration via `ProjectConfig.from_directory()` and appends project rules to the system prompt.

## Import Reference

```python
from chimera.cli.main import build_parser, main, create_parser
from chimera.cli.code import run_code
```
