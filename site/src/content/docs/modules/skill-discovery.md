---
title: "Skill Discovery"
description: "Skill Discovery"
---

`chimera.skills.discovery` discovers reusable agent skills stored as
`SKILL.md` files and injects them into the system prompt.

## Skill dataclass

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Unique kebab-case skill identifier |
| `description` | `str` | One-line description (≤ 1 024 chars) |
| `content` | `str` | Full markdown body of the skill file |
| `file_path` | `str` | Absolute path to the `SKILL.md` file |
| `base_dir` | `str` | Directory containing the file |

## SKILL.md format

```markdown
---
name: run-tests
description: "Run the project test suite and interpret failures"
---
Use `uv run pytest -x` to run tests. On failure, read the traceback
and propose a minimal fix before re-running.
```

Name must match `^[a-z0-9][a-z0-9-]{0,63}$`.  Missing `name` or
`description` fields cause the file to be silently skipped.

## Functions

| Function | Description |
|----------|-------------|
| `discover_skills(search_paths)` | Walk directories for `SKILL.md` files; later paths override earlier ones by name |
| `default_search_paths(workdir)` | Return `[{workdir}/.chimera/skills/, ~/.chimera/skills/]` in priority order |
| `format_skills_for_prompt(skills)` | Render a `## Available Skills` bullet list for system prompt injection |

## Search path priority

1. `{workdir}/.chimera/skills/` — project-local (highest priority)
2. `~/.chimera/skills/` — user global

When the same skill name appears in multiple paths, the last path wins.

## Example

```python
from chimera.skills.discovery import discover_skills, default_search_paths, format_skills_for_prompt

skills = discover_skills(default_search_paths("/my/project"))
section = format_skills_for_prompt(skills)
# Inject `section` into the agent system prompt
print(section)
# ## Available Skills
# - **run-tests**: Run the project test suite and interpret failures
```
