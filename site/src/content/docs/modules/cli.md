---
title: "CLI & REPL"
description: "CLI & REPL"
---

Chimera's command-line interface ships three families of subcommands:

1. **Synthesis & evaluation** — `synthesize`, `eval`, `bench`, `code`,
   `review`, `ci-fix`, `research`, `docs`, `testgen`, `migrate`,
   `plugins`, `auth`.
2. **Seven codename coding agents** — `mink` (`tui`), `otter`
   (`multi`), `ferret` (`sandbox`), `weasel` (`mini`), `shrew`
   (`tiny`), `stoat` (`shell`), `badger` (`strict`). Each is its own
   harness; aliases work as drop-in replacements.
3. **Cross-CLI helpers** — `which`, `doctor`, `config`, `agents`,
   `resume`, `tier-status`, `completion`, `team`, `fs`.

The `chimera code` subcommand launches an interactive REPL with 19
slash commands for session management, debugging, and agent control.

## Quick Start

```bash
# Pick a coding-agent CLI for a task (heuristic recommender, no LLM)
chimera which --task "review a PR with a sandbox"

# Diagnose your setup
chimera doctor

# Launch the interactive coding REPL
chimera code --model claude-sonnet-4-6

# Resume the most recent run from any CLI
chimera resume

# Run AI code review on a diff
chimera review --diff changes.patch
```

## Synthesis & evaluation subcommands

| Command | Description | Key flags |
|---------|-------------|-----------|
| `chimera synthesize` | Synthesize code from spec + tests | `--spec`, `--tests`, `--model`, `--strategy`, `--max-iterations`, `--max-cost`, `--patience`, `--output`, `--provider` |
| `chimera synth` | Alias for `synthesize` | (same as above) |
| `chimera eval` | Evaluate against benchmarks | `--benchmark` (required), `--dataset`, `--limit`, `--model`, `--output` |
| `chimera bench` | Run benchmark suites | `--suite` (required), `--tasks-dir`, `--model`, `--output` |
| `chimera code` | Interactive REPL | `--model`, `--workdir`, `--max-steps`, `--mode`, `--models`, `--max-cost` |
| `chimera review` | AI code review | `--diff` (required), `--model`, `--max-rounds` |
| `chimera ci-fix` | Diagnose / fix CI failures | `--log` (required), `--model`, `--max-attempts` |
| `chimera research` | Research a question | `--question` (required), `--model`, `--workdir` |
| `chimera docs` | Generate API documentation | `--source` (required), `--output` |
| `chimera testgen` | Generate test skeletons | `--source` (required), `--output` |
| `chimera migrate` | Apply migration presets | `--source` (required), `--preset` (required) |
| `chimera plugins` | Manage plugins | positional: `action` (`search` / `install` / `uninstall` / `list`); `--cli`, `--scope`, `--index`, `--overwrite`, `--legacy-entrypoints` |
| `chimera auth login <provider>` | OAuth / API-key login | provider in {`anthropic`, `openai`, `openrouter`, `xai`}. See [Auth → Provider authentication matrix](/modules/auth/#provider-authentication-matrix-wave-11-b3) for which providers run a real device flow. |
| `chimera auth logout <provider>` | Clear stored credentials | — |

## Coding-agent subcommands (7 codenames)

Each codename is a full harness. Aliases (`tui`, `multi`, `sandbox`,
`mini`, `tiny`, `shell`, `strict`) are exact drop-in replacements
selected by user posture.

| Command | Alias | Posture |
|---|---|---|
| `chimera mink` | `tui` | TUI-first interactive coding agent |
| `chimera otter` | `multi` | Server-first multi-client (HTTP + SSE + ACP) |
| `chimera ferret` | `sandbox` | Sandbox-first IDE-flagship coding agent |
| `chimera weasel` | `mini` | Minimal harness, four operating modes |
| `chimera shrew` | `tiny` | Tuned for small local models |
| `chimera stoat` | `shell` | Shell-mode toggle, Kimi-tuned defaults |
| `chimera badger` | `strict` | Harness-rewrite posture, parity tracking |

`chimera agents` (the top-level discovery command, distinct from each
CLI's own `agents` subcommand) lists every codename + alias +
inspiration so users can pick without grepping the README.

## Cross-CLI helpers

| Command | Description | Key flags |
|---|---|---|
| `chimera which --task "<freeform>"` | Heuristic CLI recommender. Pure stdlib, no LLM, no network — runs instantly in CI / shell completion. | `--task` (required), `--top-k`, `--format text\|json` |
| `chimera doctor` | Probe API keys, four local model daemons, Docker, four optional extras, all seven CLIs, the eventlog directory, and the plugin marketplace index. | `--format text\|json`, `--strict` |
| `chimera config get \| set \| unset \| list \| edit` | Persistent CLI defaults in `~/.chimera/config.toml`. Keys are dot-namespaced (`otter.model`, `global.no-color`, `shrew.vram-gb`); bare keys auto-bucket to `global`. | `--cli` (filter on `list`) |
| `chimera resume [id]` | Auto-detect which codename minted a session and dispatch to that CLI's `--resume`. Omit the id to resume the newest run across all CLIs. | passthrough flags after `--` |
| `chimera tier-status` | Render `docs/tier-status.json` (77 features × 12 categories × 3 tiers). | `--format text\|json\|md`, `--regen`, `--category` |
| `chimera completion <shell>` | Print a completion script for `bash`, `zsh`, or `fish`. The generator walks the live parser at runtime, so newly-registered subcommands show up automatically. | `<shell>` (required) |
| `chimera completion install` | Auto-detect `$SHELL`, write the script to `~/.chimera/completion/<shell>.sh` (or `~/.config/fish/completions/chimera.fish`), and append a marker-bracketed source line to the user's rc file. Idempotent; reversible via `--undo`. | `--shell`, `--rc-path`, `--undo`, `--dry-run` |
| `chimera fs ...` | Filesystem helpers (read, list, hash, etc.) used by other subcommands. | per-subcommand |
| `chimera team ...` | Experimental multi-agent teams (gated by `CHIMERA_EXPERIMENTAL_AGENT_TEAMS`). | per-subcommand |

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
| `run_code(args)` | `chimera.cli.code` | Run the interactive coding REPL (with cost gating) |
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
| `run_auth(args)` | `chimera.cli.main` | Execute the auth login / logout / list / status command |
| `add_subparser(subparsers)` | `chimera.cli.which_cmd` | Register the heuristic recommender (`chimera which`) |
| `add_subparser(subparsers)` | `chimera.cli.tier_status` | Register the tier-status renderer (`chimera tier-status`) |
| `add_arguments(parser)` | `chimera.cli.doctor` | Wire the `chimera doctor` flags |
| `register(subparsers)` | `chimera.cli.config_cmd` | Register the `chimera config` get/set/unset/list/edit subcommand |
| `add_arguments(parser)` | `chimera.cli.resume_cmd` | Wire `chimera resume <id>` (auto-detects originating codename) |
| `add_arguments(parser)` | `chimera.cli.completion` | Wire `chimera completion <shell> [install]` |
| `resolve_default(cli, key, fallback)` | `chimera.cli.config_loader` | Read a persistent default from `~/.chimera/config.toml` |
| `estimate_cost(model, prompt, expected_output_tokens)` | `chimera.cli.cost_estimator` | Pre-flight cost estimate; raises `ModelNotPriced` for unknown ids |

## Integration

- **Entry point**: The CLI is registered as a console script entry point (`chimera`), backed by `chimera.cli.main:main`.
- **Provider auto-detection**: All subcommands that accept `--model` use `create_provider()` from `chimera.providers.factory` to auto-detect the provider from the model name.
- **REPL components**: The REPL wires together `Agent`, `Session`, `ReAct` loop, `LoopConfig`, `CostTracker`, `ConsoleStreamHandler`, and `LocalEnvironment`.
- **MCP integration**: The REPL auto-loads MCP servers from `~/.chimera/mcp.json` using `MCPToolSource.from_config()`.
- **Project context**: The REPL auto-discovers project configuration via `ProjectConfig.from_directory()` and appends project rules to the system prompt.
- **Cost gating** (W12-9): The REPL refuses turns whose pre-flight estimate exceeds `--max-cost`; `/max-cost <usd>` and `/force-send` adjust the cap mid-session. See [Cost Tracking → Pre-flight estimation](/modules/cost-tracking/#pre-flight-cost-estimation).
- **Persistent defaults** (W11 A2): `chimera config set` writes `~/.chimera/config.toml`. CLI subcommands opt in via `resolve_default("<cli>", "<key>", fallback=...)`.
- **Late-bound subparsers**: `which`, `doctor`, `tier-status` register inside `try / except` blocks so a broken module never breaks `chimera --help`. The dispatcher mirrors the same guard.

## Import Reference

```python
from chimera.cli.main import build_parser, main, create_parser
from chimera.cli.code import run_code
from chimera.cli.config_loader import resolve_default
from chimera.cli.cost_estimator import (
    CostEstimate,
    ModelNotPriced,
    estimate_cost,
    format_estimate,
)
from chimera.cli.resume_cmd import (
    KNOWN_CODENAMES,
    detect_codename,
    find_latest_across_all,
)
from chimera.cli.which_cmd import KEYWORD_MAP, recommend, Recommendation
```
