---
title: "CLI & REPL API Reference"
description: "Reference for chimera.cli — top-level subcommands, REPL slash commands, and REPL run modes."
---

The CLI lives under `chimera.cli`. Every subcommand is wired up by
`chimera.cli.main.build_parser()` and dispatched by `main()`; the
interactive REPL itself lives in `chimera.cli.code`.

For a narrative tour with examples, see
[modules/cli](/modules/cli/). This page is the canonical export list.

## `chimera.cli.main`

| Symbol | Purpose |
|---|---|
| `build_parser()` | Builds the top-level `argparse.ArgumentParser` with all subcommands attached. |
| `main(argv=None)` | CLI entry point. Returns the process exit code. |
| `run_synthesize(args)` | Implements `chimera synthesize` (alias `synth`). |
| `run_eval(args)` | Implements `chimera eval`. |
| `run_bench(args)` | Implements `chimera bench`. |
| `run_review(args)` | Implements `chimera review`. |
| `run_ci_fix(args)` | Implements `chimera ci-fix`. |
| `run_research(args)` | Implements `chimera research`. |
| `run_docs(args)` | Implements `chimera docs`. |
| `run_testgen(args)` | Implements `chimera testgen`. |
| `run_migrate(args)` | Implements `chimera migrate`. |
| `run_plugins(args)` | Implements `chimera plugins`. |
| `run_auth(args)` | Implements `chimera auth`. |

## `chimera code` — the interactive REPL

`chimera.cli.code` runs the REPL, owning the readline-idle vs. raw-stdin
running mode switch, the threaded agent loop, and the slash-command
dispatcher.

### Top-level flags

| Flag | Description |
|------|-------------|
| `--model <name>` | Single model (overrides `ANTHROPIC_MODEL`). |
| `--models <list>` | Comma-separated list to cycle through with `/model next` / `/model prev`. |
| `--mode interactive\|rpc\|json` | Terminal mode. `interactive` is the default readline REPL; `rpc` speaks JSON-RPC 2.0 over stdio; `json` emits newline-delimited JSON objects. |
| `--workdir <path>` | Working directory for tool execution. |
| `--max-steps <n>` | Cap on ReAct iterations per turn. |

### Slash commands

| Command | Action |
|---|---|
| `/help` | List slash commands and their arguments. |
| `/model [next\|prev\|<name>]` | Show or switch the active model. With `--models`, `next`/`prev` cycle through the list. |
| `/cost` | Print the running token + USD cost for the session. |
| `/clear` | Clear the screen. |
| `/history` | Dump the conversation history. |
| `/tools` | List the tools available to the agent. |
| `/context` | Show context-window usage and the active compaction strategy. |
| `/debug` | Toggle debug-event printing. |
| `/session` | Show session id, save path, and resume hint. |
| `/compact` | Force one compaction pass on the conversation. |
| `/audit` | Print the permission audit log for the current session. |
| `/checkpoint [<name>]` | Save (or restore-by-name with `--restore <name>`) a checkpoint. |
| `/agent <preset>` | Switch the active `CodingAgent` preset (`coding_agent`, `codex`, `kimi`, `swebench`, `minimal`, `explore`). |
| `/init` | Generate a `CLAUDE.md` for the current repo (uses the `init` skill). |
| `/yolo` | Switch into `--permission-mode yolo` for the rest of the session. |
| `/tree` | Print the full session-tree managed by `SessionTree`. |
| `/branch <name>` | Fork the current session into a new named branch. |
| `/switch <name>` | Switch to an existing branch in the session tree. |
| `/exit` | Exit the REPL. |

Mid-turn steering via Ctrl-C interrupts the active step, drains the
streamer, and surfaces a prompt for the next user message; sessions are
auto-saved to `~/.chimera/sessions/` after every turn.

## See also

- [Configure Permissions](/configure-permissions/) for the
  `--permission-mode` flag exposed by the wider CLI matrix.
- [`chimera.sessions`](/reference/sessions/) for `SessionTree` semantics.
- [`chimera.providers`](/reference/providers/) for model-name routing.
