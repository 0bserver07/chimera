# `chimera otter` rules ingest

Otter ingests project-level coding-style and behavioral rules from a
small set of conventional sources and concatenates them into one
markdown blob that is injected into the agent's system prompt. This is
the otter twin of mink's `CLAUDE.md` ingest.

## Sources

The loader (`chimera/otter/rules.py`) walks five roots in increasing
precedence order — later entries override earlier ones when there's a
header collision, but in practice all sources are concatenated:

1. `~/.opencode/AGENTS.md` — user-level baseline.
2. `~/.config/opencode/AGENTS.md` — XDG user-level baseline.
3. `<project_root>/AGENTS.md` — project-level rules.
4. `<project_root>/.cursor/rules/*.mdc` — cursor-style rules
   (one file per rule); files are ingested in lexical order.
5. `<project_root>/.opencode/rules.md` — project rules at the
   `.opencode/` scope.

YAML-ish frontmatter (`---` ... `---` at the top of the file) is
stripped before concatenation.

## Output shape

`load_otter_rules(project_root)` returns a single string suitable for
direct injection into a system prompt. Each source is wrapped in a
fenced block whose header indicates origin:

```
<!-- AGENTS.md (project) -->
You are working on the chimera project. ...

<!-- .cursor/rules/python-style.mdc -->
Prefer dataclasses over TypedDicts when fields ...

<!-- .opencode/rules.md -->
Always run /lint after edits.
```

These HTML-style comments are kept legible to the agent (helps the LLM
attribute guidance to a source) and ignored by markdown renderers.

## Truncation

The combined output is capped at `DEFAULT_MAX_CHARS` (10,000 by
default) to keep the system prompt reasonable. When the cap is hit,
the loader appends `[...truncated]` to the cut point and stops. The
order of sources is the order of importance (user → project →
.opencode), so project-level rules win on truncation.

Override the cap with the `max_chars` argument:

```python
from chimera.otter.rules import load_otter_rules

text = load_otter_rules("/path/to/project", max_chars=20_000)
```

## Frontmatter handling

Frontmatter is stripped to keep the prompt focused on prose. The
loader does not try to parse YAML keys (no PyYAML dependency); it
just removes the `---`-delimited block at the head of each file when
present.

If a `.cursor/rules/<file>.mdc` declares a `description:` in its
frontmatter, the loader currently ignores it. Cursor's
"only-when-X-applies" semantics are not honored at this layer; every
rule file is included. Selective inclusion is a follow-up.

## Cursor `.mdc` files

`.cursor/rules/*.mdc` is the convention introduced by the Cursor
editor for shipping per-project AI rules. Otter reads these files in
lexical filename order so projects can prefix with `00-`, `10-`,
`20-` to control concatenation order.

A typical `.cursor/rules/` directory might look like:

```
.cursor/rules/
  00-style.mdc
  10-testing.mdc
  20-naming.mdc
  90-do-not.mdc
```

All four files concatenate into the system prompt in that order.

## Discovery

`discover_rule_files(project_root)` returns the list of paths the
loader will read in the order it will read them. Useful for
`chimera otter doctor` and IDE integrations:

```sh
chimera otter
> /doctor
rules sources:
  ~/.opencode/AGENTS.md           (1.2 KB)
  /repo/AGENTS.md                  (3.4 KB)
  /repo/.cursor/rules/00-style.mdc (0.8 KB)
  /repo/.cursor/rules/10-tests.mdc (1.1 KB)
  /repo/.opencode/rules.md         (0.4 KB)
total: 6.9 KB / 10.0 KB cap
```

## Wiring through the agent

The rules text is concatenated to the agent's system prompt at session
bootstrap. Subsequent agent switches mid-session do **not** re-read
the files; restart the REPL to pick up edits. (This matches mink's
behavior. A `/reload` slash command is on the follow-up list.)

The agent's own `prompt:` frontmatter (see `docs/otter/agents.md`) is
appended *after* the rules blob, so per-agent guidance overrides
project-wide rules on conflict — by virtue of being closer to the end
of the system prompt, where most LLMs apply higher weight.

## Hygiene

The rules ingest deliberately does **not** evaluate code or follow
shell-style includes. It treats every file as static markdown text.
This avoids surprising file reads (the system prompt is bounded by
the discovered file set) and keeps load time under a millisecond on
typical projects.

## Test surface

`tests/otter/test_rules.py` covers:

- All five source paths discovered when present.
- User-only / project-only / mixed configurations.
- `.cursor/rules/*.mdc` lexical ordering.
- Frontmatter strip on each source.
- Truncation at the configured cap with the `[...truncated]` marker.
- Missing-source fallback (returns empty string, not an error).

## Templates worth shipping

For projects that want a quick start, the following `AGENTS.md` shape
works well:

```markdown
# Project rules

## Style
- Python 3.11+, type hints required.
- Prefer dataclasses to TypedDicts.

## Workflow
- Always run /lint and /test after edits.
- Never commit without `git status` confirming.

## Boundaries
- Do not touch `infra/terraform/` without explicit ask.
- Do not push to remotes; the user pushes.
```

Drop that into the project root and otter will fold it into the system
prompt automatically.

## See also

- `chimera/otter/rules.py` — implementation.
- `docs/otter/agents.md` — per-agent prompt extension.
- `docs/otter/commands.md` — custom commands for repeatable workflows.
- `docs/otter/parity-matrix.md` — overall parity status.
