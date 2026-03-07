# Use the REPL

Get productive with `chimera code`, the interactive coding session that gives
you a conversational agent with full tool access.

---

## Prerequisites

```bash
pip install chimera-ai[anthropic]
export ANTHROPIC_API_KEY="sk-ant-..."
```

---

## Starting the REPL

```bash
chimera code --model claude-sonnet-4 --workdir ./my-project
```

You will see a startup banner like:

```
chimera code v0.1.0 | model: claude-sonnet-4 | /help for commands
```

The `--workdir` flag sets the working directory for all file operations.
Omit it to default to the current directory.  `--max-steps 80` raises the
per-turn tool-call limit (default 50).

---

## Asking Questions

Type naturally at the `>` prompt.  The agent has access to all 16 built-in
tools (read, write, edit, bash, search, git, etc.) and will use them
automatically.

```
> Find all TODO comments in the codebase and list them by file

> Refactor the User class to use dataclasses

> Run the test suite and fix any failures
```

After each turn, the REPL prints cost and step count:

```
  [cost: $0.0312 | steps: 4]
```

Press `Ctrl-C` to interrupt a long-running turn.  Press `Ctrl-D` or type
`/exit` to quit.

---

## Slash Commands

All commands start with `/` and support tab completion.

### Information

| Command | Description |
|---------|-------------|
| `/help` | List all available commands. |
| `/model` | Show the current model name. |
| `/cost` | Show total cost, per-model breakdown, and remaining budget. |
| `/context` | Show message count and estimated token usage. |
| `/history` | Show the last 10 messages (role + first 80 chars). |
| `/tools` | List all loaded tools with descriptions. |

### Session Control

| Command | Description |
|---------|-------------|
| `/clear` | Clear the entire conversation context and start fresh. |
| `/compact` | Compress context when the token window fills up. |
| `/debug` | Toggle debug mode on/off (shows raw tool calls). |
| `/exit` | Quit the REPL and print total session cost. |

### Persistence

| Command | Description |
|---------|-------------|
| `/session save my-feature` | Save the current session with an optional name. |
| `/session list` | Show session management options. |
| `/session fork` | Fork the session (branch the conversation). |

### Checkpoints

| Command | Description |
|---------|-------------|
| `/checkpoint save before-refactor` | Create a named checkpoint. |
| `/checkpoint list` | List all checkpoints with IDs and timestamps. |
| `/checkpoint restore before-refactor` | Roll back to a named checkpoint. |
| `/checkpoint undo` | Undo to the most recent checkpoint. |

### Audit and Agents

| Command | Description |
|---------|-------------|
| `/audit` | Show a summary of tool execution decisions (allowed/denied counts). |
| `/audit clear` | Clear the audit log. |
| `/agent list` | List available agent presets (build, review, explore, etc.). |

---

## Tips

- **Watch your costs.** Run `/cost` periodically.  The breakdown shows
  spending per model so you can spot expensive turns.
- **Compact before you hit limits.** When `/context` reports high token
  counts, run `/compact` to compress the history and free up context window
  space.
- **Save before risky changes.** Use `/checkpoint save` before asking the
  agent to do large refactors.  Restore if things go sideways.
- **Use sessions across days.** `/session save my-feature` persists the
  full conversation.  Resume it tomorrow without losing context.
- **History is saved automatically.** Readline history is written to
  `~/.chimera/history` (up to 1000 entries) and restored on next launch.

---

## Next Steps

- [Add Security Policies](add-security-policies.md) -- control what the agent
  is allowed to do.
- [Build a Coding Agent](build-a-coding-agent.md) -- use the same components
  programmatically.
