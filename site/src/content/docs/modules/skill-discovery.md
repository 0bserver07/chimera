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
| `default_search_paths(workdir)` | Return the three default skill paths in priority order |
| `bundled_algorithms_path()` | Return the path to the bundled algorithm skills shipped with the package |
| `format_skills_for_prompt(skills)` | Render a `## Available Skills` bullet list for system prompt injection |
| `default_remote_cache()` | Return `~/.chimera/cache/skills` (the default remote cache root) |
| `fetch_remote_index(index_url)` | Download and parse a remote `index.json` skill manifest |
| `download_remote_skills(index_url)` | Download every skill from a remote index into the local cache and return them |

## Search path priority

1. `bundled_algorithms_path()` — bundled chimera algorithm skills (G12), read-only
2. `{workdir}/.chimera/skills/` — project-local
3. `~/.chimera/skills/` — user global

When the same skill name appears in multiple paths, the **later** path
wins.  This mirrors the rest of Chimera's project-over-user precedence
model: a project skill named `algo-binary-search` overrides the bundled
version, and a user skill of the same name overrides the project
version.

## Bundled algorithm skills (G12)

`chimera.skills.algorithms` ships 13 algorithm cheat-sheet skills
covering binary search, dynamic programming, BFS, DFS, hash, two
pointers, sliding window, sorting, greedy, recursion, graph traversal,
math tricks, and string algorithms.  Each lives in its own subdirectory
with one `SKILL.md`, picked up automatically via
`default_search_paths()`.

## Remote skill index

`download_remote_skills()` fetches an `index.json` manifest, downloads
each entry's `SKILL.md` into a local cache, and returns the freshly-cached
`Skill` list.  The expected manifest schema is:

```json
{
  "skills": [
    {"name": "lint-feedback", "description": "...", "url": "https://example.com/skills/lint-feedback/SKILL.md"},
    {"name": "test-coverage", "description": "...", "url": "https://example.com/skills/test-coverage/SKILL.md"}
  ]
}
```

A bare list (`[{...}, ...]`) without the envelope is also accepted.
Names must match `^[a-z0-9][a-z0-9-]{0,63}$`; URLs must use `http`/`https`.
Individual download failures are skipped so one broken entry never
poisons the index.

```python
from chimera.skills.discovery import download_remote_skills

skills = download_remote_skills(
    "https://example.com/chimera-skills/index.json",
    overwrite=False,           # skip skills already cached
    timeout=10.0,
)
```

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
