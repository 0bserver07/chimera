---
title: Weasel Prompt Templates
description: Prompt-template registry for chimera weasel — markdown-with-frontmatter system prompts plus optional user-message prefixes and metadata.
---

# Weasel Prompt Templates

Prompt templates are a core extension surface for `chimera weasel`. Each
template bundles four things under one named identifier:

- **`name`** — the lookup key consumed by `--prompt-template <name>`.
- **`system_prompt`** — the markdown body of the file. Used as the
  agent's system prompt verbatim.
- **`user_prefix`** — an optional string spliced in front of every user
  turn. Empty by default.
- **`metadata`** — arbitrary frontmatter keys not consumed by the
  fields above. Preserved verbatim for routing tags, owner emails, etc.

A built-in `default` template ships in the box. Drop additional `.md`
files under `~/.weasel/prompts/` (user scope) or
`<project>/.weasel/prompts/` (project scope) to register more — project
scope wins on name collision.

## Selecting a template

```bash
chimera weasel --prompt-template review -p "audit auth.py"
WEASEL_PROMPT_TEMPLATE=tester chimera weasel -p "write tests for foo.py"
```

Unknown names fall back to `default` rather than erroring, and a
malformed file is silently skipped — a single bad template cannot break
a weasel invocation.

## File shape

```markdown
---
name: review
description: Strict code-review system prompt.
user_prefix: "Review the following diff: "
tags: [review, lint]
strict: true
---
You are a meticulous reviewer. Focus on correctness and clarity.
Flag bugs, surface unclear naming, and suggest concrete fixes.
```

The frontmatter is parsed with a tiny stdlib YAML-subset reader:
`key: value` pairs where the value is a string (quoted or unquoted), an
int, a float, a boolean (`true`/`false`/`yes`/`no`), `null`, or an
inline list (`[a, b, c]`). The body following the closing `---` is
the system prompt (whitespace-stripped). Files without frontmatter use
the file stem as the template name and the entire file as the system
prompt.

### Reserved frontmatter keys

| Key | Type | Effect |
| --- | --- | --- |
| `name` | string | Template identifier (defaults to file stem). |
| `user_prefix` | string | Prefix spliced before every user turn. |

Every other frontmatter key is preserved verbatim under
`PromptTemplate.metadata` and is free for embedder use (`description`,
`tags`, `owner`, `strict`, …).

## Discovery roots

Walked in this order, with later entries overriding earlier ones on
name collision:

1. **Built-ins:** `default`.
2. **User scope:** `~/.weasel/prompts/*.md`.
3. **Project scope:** `<project_root>/.weasel/prompts/*.md`.

Hidden files (`.foo.md`) and non-`.md` files are skipped. A file that
parses to an empty body and empty frontmatter is dropped (rather than
stored as a no-op template).

## Built-in default

The built-in `default` system prompt matches the literal that the bare
`chimera weasel -p` path uses when no flag is set:

> You are Weasel, a minimal Chimera coding agent. Use tools to inspect
> and modify the user's repo. Be concise.

Override it by writing a `default.md` under either discovery root.

## Programmatic API

```python
from pathlib import Path

from chimera.weasel.prompt_templates import (
    PromptTemplate,
    get_prompt_template,
    load_prompt_templates,
)

# One-shot lookup against the module-level built-in cache:
template = get_prompt_template("default")
print(template.system_prompt)

# Discover everything under both scope roots:
registry = load_prompt_templates(Path.cwd())
review = registry["review"]
print(review.system_prompt)
print(review.user_prefix)
print(review.metadata.get("tags"))

# Pass a registry to get_prompt_template to honor on-disk overrides:
template = get_prompt_template("review", registry=registry)
```

`PromptTemplate` is a stdlib `dataclass`. The whole module is
dependency-free, so embedders can pull it in without importing the
rest of chimera.

## Composing with themes

Themes (see [`themes.md`](themes.md)) and prompt templates are
orthogonal: themes restyle the REPL chrome, templates swap the agent's
instructions. They share the same discovery model
(`<project>/.weasel/<kind>/` and `~/.weasel/<kind>/`), so a single
project can ship a matched pair under one `.weasel/` tree and select
both at once:

```bash
chimera weasel --theme solarized --prompt-template review -p "..."
```
