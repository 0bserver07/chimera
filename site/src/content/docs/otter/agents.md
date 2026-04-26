---
title: Otter Agents
description: Built-in otter agent presets and custom-agent loading from .opencode/agent/*.md.
---

# `chimera otter` agents

Otter agents are markdown files (or built-in presets) that define a named
agent identity: the system prompt, default tools, default model, and
permission shape. The `chimera otter` runtime walks four sources in
order, with project-level definitions winning over user-level which in
turn win over the built-in presets:

1. `<project_root>/.opencode/agent/<name>.md`
2. `~/.opencode/agent/<name>.md`
3. `<project_root>/.opencode/agents/<name>.md` (plural alias)
4. Built-in presets registered in `chimera.agents.loader.create_default_registry()`.

This is the otter twin of mink's `~/.claude/agents/` discovery (see
`docs/mink/subagents.md`). The on-disk schema is intentionally compatible
so a project can drop the same markdown into either tree and have it
resolve.

## Markdown schema

```markdown
---
name: reviewer
description: "Code reviewer that checks the diff against the project rules."
mode: subagent          # subagent | primary | all
model: anthropic/claude-sonnet-4-6
tools: [bash, read, edit, grep]
permission:
  edit: ask
  bash: deny
---

You are a careful code reviewer. Read the diff and report each issue
with a file path, line number, and severity. ...
```

YAML-ish frontmatter is parsed by a stdlib-only loader (no PyYAML
dependency); arrays and nested objects are supported via the same
permissive parser used by `chimera/otter/rules.py`. The body of the
markdown becomes the agent's system prompt.

Recognized frontmatter keys:

- **`name`** *(string, required)* — registry key. Slash commands and
  `--agent <name>` resolve through this.
- **`description`** *(string)* — shown by `chimera otter agents list`.
- **`mode`** *(`subagent` | `primary` | `all`)* — `subagent` agents are
  invocable only via the `task` / delegate tool; `primary` agents drive
  top-level sessions; `all` is both.
- **`model`** *(string)* — `provider/model` form, e.g. `openai/gpt-4o`.
  Falls back to the session's default model when omitted.
- **`tools`** *(list of tool names)* — narrows the tool group. Names
  are matched case-insensitively against the tool registry. Omit to
  inherit `AGENT_TOOLS`.
- **`permission`** *(object)* — per-permission `allow|ask|deny` map.
  Merged on top of the session-wide ruleset.
- **`hidden`** *(bool)* — skips the agent in `agents list` output;
  still resolvable by name.

The body of the file (everything after the closing `---`) is treated
as the system prompt. References to `${...}` template variables are
resolved by `chimera.core.prompt`.

## Built-in presets

Otter inherits Chimera's built-in agent presets via
`chimera.agents.loader.create_default_registry()`:

| Name      | Mode    | Description                                                    |
|-----------|---------|----------------------------------------------------------------|
| `build`   | primary | Default agent. Executes tools per the configured permissions.  |
| `plan`    | primary | Plan mode. Forbids edit/write tools; produces a markdown plan. |
| `explore` | primary | Read-only exploration. No edit/write/bash.                     |
| `review`  | subagent| Code reviewer for `git diff HEAD` style changes.               |
| `general` | all     | General-purpose agent for multi-step tasks.                    |

Presets are defined in code (not markdown) so they always exist even
without a project tree. Markdown files at the project or user level can
override a preset by reusing the same `name`.

## CLI surface

```sh
# List agents (project + user + built-in, deduplicated by name).
chimera otter agents list

# Show one agent's resolved view (frontmatter + final system prompt).
chimera otter agents show reviewer

# Use a specific agent for a one-shot turn.
chimera otter -p "review the staged changes" --agent reviewer

# Use a specific agent in the REPL.
chimera otter
> /agent reviewer
> please review the staged changes
```

`chimera otter agents list` prints a three-column table:

```
NAME       MODE       SOURCE
build      primary    built-in
explore    primary    built-in
plan       primary    built-in
review     subagent   built-in
reviewer   subagent   .opencode/agent/reviewer.md
general    all        built-in
```

Source paths are made relative to the cwd for project-level definitions
and rendered as `~/.opencode/agent/<name>.md` for user-level ones.

`chimera otter agents show <name>` prints the merged frontmatter as
JSON, then the rendered system prompt with `${...}` placeholders
substituted. This is the same view the runtime sees, so it doubles as a
debugging aid when an agent behaves unexpectedly.

## Wiring through the REPL

The REPL exposes two slash commands for agents:

- `/agent` (alias `/agents`) — list agents (same as the subcommand).
- `/agent <name>` — switch the active session's agent without
  restarting the REPL.

Switching mid-session preserves the conversation history and the
working-directory file tracker; only the system prompt, tool group,
and permission ruleset are swapped out.

## Agent generation (deferred)

The upstream open-source coding agent ships `agent create` for
interactive agent scaffolding. Otter wave-1 ships `agents list` and
`agents show` only — `agents create` is tracked as a follow-up. The
workaround is to copy an existing agent file and edit the frontmatter:

```sh
mkdir -p .opencode/agent
cp ~/.opencode/agent/build.md .opencode/agent/my-agent.md
$EDITOR .opencode/agent/my-agent.md
```

## Subagents and delegation

Agents marked `mode: subagent` (or `all`) are invocable from a parent
agent through the `task` tool. The parent passes a prompt string and
receives the subagent's final reply as a tool result. This mirrors the
upstream agent's "primary vs subagent" split and is implemented on top
of `chimera.tools.task_tool` and `chimera.cli.agent_teams`.

Example: a `build` (primary) session delegates to the `review` subagent
mid-turn:

```sh
chimera otter -p "implement feature X then run /review on the diff"
```

`/review` here is a custom command (see `docs/otter/commands.md`) that
invokes `task review --prompt "<the diff>"` under the hood.

## Test surface

`tests/otter/test_agents.py` covers:

- Built-in-only listing when no markdown agents are present.
- Project + user discovery with file-system fixtures.
- Project precedence: a project-level agent shadows a user-level agent
  with the same name.
- `find_agent` miss path returns `None`.
- `cmd_agents_list` formatting and `cmd_agents_show` happy path.
- Empty-table fallback so the CLI never crashes when no agents are
  configured.

## See also

- `chimera/otter/agents.py` — implementation.
- `docs/otter/commands.md` — custom commands ingested from
  `.opencode/command/*.md`.
- `docs/otter/rules.md` — system-prompt rules ingested from `AGENTS.md`
  and `.cursor/rules/*.mdc`.
- `docs/otter/parity-matrix.md` — overall parity status.
