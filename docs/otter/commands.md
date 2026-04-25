# `chimera otter` custom commands

Custom commands are reusable prompt templates that live as markdown
files under `.opencode/command/<name>.md` (project-level) or
`~/.opencode/command/<name>.md` (user-level). They show up as slash
commands in the otter REPL and can be invoked with arguments.

This is the otter twin of mink's `.claude/commands/` ingest. The schema
is intentionally compatible so a project can drop the same markdown
into either tree and have it resolve.

## Markdown schema

```markdown
---
description: "Review the staged diff against the project rules."
agent: review
model: anthropic/claude-sonnet-4-6
args:
  - name: target
    description: "What to review (default: staged)"
    default: staged
---

You are the project reviewer. Read the diff for ${target} and produce
a list of issues with file paths, line numbers, and severities.
```

YAML-ish frontmatter is parsed by the same stdlib loader used by
`chimera/otter/rules.py` and `chimera/otter/agents.py`. The body is
the prompt template with `${name}` placeholders that are filled in
from the command-line argument list.

Recognized frontmatter keys:

- **`description`** *(string)* — shown by `/help` next to the command.
- **`agent`** *(string)* — agent identity to invoke. Falls back to the
  session's current agent when omitted.
- **`model`** *(string)* — `provider/model` override. Falls back to
  the agent's model, then the session default.
- **`args`** *(list of `{name, description, default}`)* — declarative
  argument schema. Positional arguments fill them in order; named
  arguments (`--key value`) override.
- **`subtask`** *(bool)* — when true, the command is dispatched as a
  subagent task and its output is folded back into the parent turn.
- **`hidden`** *(bool)* — keep the command out of `/help` listing.

## File layout

The loader scans four roots (in this precedence order):

1. `<project_root>/.opencode/command/<name>.md`
2. `~/.opencode/command/<name>.md`
3. `<project_root>/.opencode/commands/<name>.md` (plural alias)
4. `~/.opencode/commands/<name>.md` (plural alias)

Project entries shadow user entries on name conflict, so a team can
ship project-specific overrides of personal commands without touching
each developer's home directory.

The plural / singular variants are accepted because both spellings are
common in the wild. Internally they collapse into one registry keyed
by `name` (the filename stem).

## Built-in commands

Two built-in commands ship out of the box (mirrors the upstream
defaults):

| Name     | Description                                  |
|----------|----------------------------------------------|
| `/init`  | Guided `AGENTS.md` setup for the project.    |
| `/review`| Review the staged or current diff.           |

Both are sourced from `chimera.cli.code` and registered through the
shared slash registry, so they exist even without a `.opencode/`
directory present.

## Invoking commands

```sh
chimera otter
> /review              # default args
> /review HEAD~1       # positional arg fills $target
> /review --target staged
```

A custom command can also be invoked from a one-shot prompt by
embedding it in the prompt body:

```sh
chimera otter -p "/review HEAD~1 then summarize the findings"
```

When the parser sees a leading `/<name>`, it expands the template
in-place using the recorded `args` schema and feeds the result to the
agent. `${ARGUMENTS}` (the literal token, kept for upstream
compatibility) expands to the raw argument string.

## Argument templating

Two placeholder styles are honored:

- **`${name}`** — named placeholder, filled from `args` schema.
- **`$1`, `$2`, ...** — positional placeholders, filled from the
  raw argument list (zero-indexed by convention but written
  one-indexed for readability). Both styles can mix in one template.
- **`$ARGUMENTS`** — the entire raw argument string (the upstream
  spelling is honored verbatim).

Missing placeholders default to the value declared in `args[].default`,
or to an empty string if no default is set.

## Discovery via `/help`

`/help` (the shared shared slash command) walks the registered command
table and prints the `description` next to each name. Custom commands
appear after the built-ins, sorted alphabetically. Commands marked
`hidden: true` are suppressed.

## Wiring under the hood

The loader (`chimera/otter/commands.py`) returns a dict of
`CommandRecord` dataclasses. The REPL bootstraps merge that dict into
the shared slash registry under names like `cmd_<name>`, so `/review`
becomes an alias for the loader-built handler. Each handler is a
closure over the template and the `args` schema; argument parsing is
permissive (unknown args are dropped with a warning rather than an
error so the REPL stays usable).

## Test surface

`tests/otter/test_commands.py` covers:

- Project-level discovery with frontmatter.
- User-level discovery as a fallback.
- Project-overrides-user precedence.
- Plural / singular alias unification.
- `${name}` and `$1` placeholder expansion.
- `args[].default` filling on missing positionals.
- Loader resilience to malformed frontmatter (warns, doesn't raise).

## Templates worth shipping

A few templates that work well under otter:

- `.opencode/command/test.md` — runs the project test suite and
  summarizes failures.
- `.opencode/command/lint.md` — runs the project linter and asks the
  agent to fix the first N findings.
- `.opencode/command/migrate.md` — drives a rule-based migration
  through `chimera.migration.MigrationPlanner`.
- `.opencode/command/explain.md` — opens a `${file}` in read-only
  mode and explains what it does.

These are not built-in; they're suggestions. Drop them into the project
tree, edit to taste, and they show up under `/help` automatically.

## See also

- `chimera/otter/commands.py` — loader implementation.
- `docs/otter/agents.md` — agent definitions consumed by `agent:` field.
- `docs/otter/slash-commands.md` — full slash palette.
- `docs/otter/rules.md` — for `AGENTS.md` ingest, which is sometimes
  used to surface "always run /review before commit"-style guidance.
