# CLAUDE.md Memory Loader

Chimera's mink harness loads `CLAUDE.md` files exactly the way Claude
Code does: a walk-up scan from the working directory, with import expansion,
path-scoped rules, and injection as a single user message immediately after
the system prompt — never folded into the system prompt itself.

Implementation lives in `chimera/context/agent_memory.py`. Public API:

```python
from chimera.context.agent_memory import (
    discover_memory_files, load_memory, inject_memory,
    resolve_imports, parse_frontmatter,
)
```

## Walk-up Algorithm

`discover_memory_files(cwd)` walks from `cwd` to the filesystem root. At each
directory level it checks (in this exact order, deduped by absolute path):

1. `CLAUDE.md` — committed project memory.
2. `CLAUDE.local.md` — gitignored local overrides.
3. `.claude/CLAUDE.md` — project memory under the `.claude/` config dir.

Levels are emitted root-most first, leaf-most last, so more-specific entries
appear later in the concatenated buffer. After the per-level pass, two
additional sources are appended:

4. `~/.claude/CLAUDE.md` — user-global memory (all projects).
5. `.claude/rules/*.md` — path-scoped rules from the closest project root,
   filtered by their `paths:` frontmatter glob (see below).

If the same absolute path appears at multiple levels (rare), it is loaded
only once — at the first level it appears.

## File Precedence and Concatenation

CC's contract: **all matched files are concatenated**, not overridden. With
later-wins ordering for any conflicting facts. Chimera follows this exactly.
Each file is wrapped with a `<!-- source: <abs-path> -->` header so the
model can cite where a piece of guidance came from.

Block-level HTML comments inside source files (`<!-- ... -->`) are stripped
before injection (ecosystem parity).

## `@import` Syntax

Inside any memory file, `@<path>` directives are expanded inline:

```
# Project conventions
@./snippets/style.md
@~/.claude/snippets/global-rules.md
```

Resolution rules:

- Relative paths (`./`, `../`, bare names) resolve against the **importing
  file's directory**, not cwd.
- `@~/...` expands via `Path.expanduser`.
- Recursion is depth-capped at **5 hops** (`max_hops` argument). At cap, the
  literal `@path` token is left in place.
- Cycles are broken by tracking visited absolute paths. The second visit
  expands to an empty string (silently dropped); the first expansion stands.
- Unreadable or missing targets leave the `@path` token verbatim in the
  output, so authors can spot broken refs.

## `paths:` Frontmatter (Path-Scoped Rules)

Rules in `.claude/rules/*.md` may declare a YAML-ish frontmatter:

```yaml
---
paths:
  - "**/*.ts"
  - "src/api/**"
---
TypeScript-specific guidance goes here.
```

A rule is injected when **any** glob matches **any** file under the current
working directory (or matches cwd's path itself). Rules with no `paths:`
frontmatter always match. The frontmatter parser is hand-written stdlib
(no PyYAML dep) and supports:

- Inline lists: `paths: ['**/*.ts', 'src/**']`
- Block lists with `- ` items.
- Scalar `key: value` pairs.

## Injection Position

`inject_memory(messages, cwd)` returns a new message list with the memory
inserted **after any leading system message(s)** and **before the first
user message**:

```
[system, MEMORY, user, assistant, user, ...]
```

The memory is wrapped in a `<memory source="CLAUDE.md">...</memory>`
sentinel so downstream compaction can recognize and protect it.

When no memory files are discovered, `inject_memory` returns a shallow copy
of the original message list (no-op).

## Frontmatter Schema

Currently parsed keys (other keys pass through as raw scalars):

| Key       | Type            | Use                                |
| --------- | --------------- | ---------------------------------- |
| `paths`   | list[str]       | Glob filter for `.claude/rules/*`  |

Future expansion (parsed but not yet acted on): `name`, `description`,
`when_to_use`, `priority`.

## Examples

```python
from pathlib import Path
from chimera.context.agent_memory import load_memory, inject_memory

# 1. Just compose the markdown.
md = load_memory(Path.cwd())
print(md)

# 2. Inject into an OpenAI/Anthropic-style message list.
msgs = [{"role": "system", "content": "You are a coding agent."}]
msgs = inject_memory(msgs, Path.cwd())
# msgs[1] is now the memory user message.
```

## Divergences from the reference implementation

- **Managed CLAUDE.md** (e.g. `/Library/Application Support/ClaudeCode/CLAUDE.md`)
  is not loaded. Org-policy memory is out of scope for the OSS harness.
- **`claudeMdExcludes`** glob exclusions from `settings.json` are not yet
  honored. Exclude unwanted files by path or by removing them.
- **Lazy subdirectory loading** (re-reading CLAUDE.md when the model reads a
  file in a sibling tree) is not implemented; one walk per session at startup.
- **First-time external-import approval dialog** is skipped — Chimera does
  not gate `@import` behind a confirmation prompt.

## Tests

`tests/core/test_agent_memory_loader.py` — 13 tests covering walk-up ordering,
`@import` resolution, cycle breaking, hop cap, `paths:` glob matching,
user-global appending, frontmatter parsing, and injection positioning.
