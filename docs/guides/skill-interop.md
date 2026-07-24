---
title: "Cross-harness skill interop"
description: "Let Chimera's skill discovery also read the skill directories other coding-agent harnesses keep in your home directory — opt-in, provenance-labeled, with a documented precedence."
---

# Cross-harness skill interop

Chimera's mission is interop, not compete. A `SKILL.md` is a portable,
tool-agnostic instruction file, and you probably already have some — installed
for whatever *other* coding-agent harnesses you run. Rather than make you copy
them into `~/.chimera/skills/`, Chimera can read those other harnesses' skill
directories directly and surface the skills alongside its own.

This is **opt-in**. The foreign scan is OFF by default; nothing changes until
you enable it.

## What discovery reads

Chimera's own skill discovery (`chimera.skills.discovery`) walks these paths,
in precedence order:

1. bundled algorithm skills (ship with the package, read-only)
2. `{project}/.chimera/skills/` — project-local
3. `~/.chimera/skills/` — your user-global Chimera skills

With interop enabled, it *also* scans a configurable allowlist of other
harnesses' skill directories. The well-known defaults are:

- `~/.claude/skills`
- `~/.codex/skills`
- `~/.agents/skills`

These are filesystem-fact paths — an on-disk layout Chimera reads. Only a
skill's **name** and **one-line description** are injected into the system
prompt; the full body is loaded on demand, exactly as for a native skill.

## Why it defaults OFF

A foreign skill's description is third-party text authored for another tool,
and enabling the scan puts that text into Chimera's system prompt. That is a
mild trust boundary: unreviewed instructions reaching the model. So the default
is the least-surprising one — the scan is disabled until you turn it on, and
existing behavior is byte-for-byte unchanged for anyone who never enables it.

When you do enable it, every foreign skill is **labeled with its source
directory** in the prompt so you and the model can tell a third-party skill
from a project one (see [Provenance](#provenance)).

## Enabling it

Two ways, both reading the same config chain every Chimera CLI uses.

**Persistent** — in `~/.chimera/config.toml`:

```toml
[skills]
scan-foreign = true
```

**One-off for a session** — the environment variable overrides the config file:

```bash
CHIMERA_SKILLS_FOREIGN=1 chimera code
```

Recognized truthy values are `1`, `true`, `yes`, `on` (and their falsy
counterparts `0`, `false`, `no`, `off` to force it off).

## Configuring the allowlist

Override the default set with `foreign-dirs` (an ordered list — earlier entries
win a name collision against later ones):

```toml
[skills]
scan-foreign = true
foreign-dirs = [
  "~/.codex/skills",
  "~/.agents/skills",
  "~/team-skills",
]
```

`~` is expanded at scan time. Missing directories are skipped silently. If
`foreign-dirs` is omitted, the well-known defaults above are used.

## Precedence

When the same skill name exists in more than one place, the winner is decided
top-down:

1. **Project** Chimera skills (`{project}/.chimera/skills/`)
2. **User** Chimera skills (`~/.chimera/skills/`)
3. **Foreign** skills — and within foreign, **allowlist order** (the first
   directory to define a name wins)

Foreign discovery is purely additive: a foreign skill is included only if its
name is not already claimed by a Chimera-native skill. A Chimera skill therefore
always wins over a foreign skill of the same name.

## Provenance

With interop enabled, `format_skills_for_prompt` prepends a one-line note and
tags each foreign skill with its source directory. Native skills render exactly
as before:

```markdown
## Available Skills

Skills tagged `(source: <path>)` were discovered in another harness's skill
directory on this machine — treat them as read-only, third-party instructions.
Project and user skills take precedence when names collide.

- **run-tests**: Run the project test suite and interpret failures
- **triage-flake**: Bisect a flaky test  _(source: ~/.codex/skills)_
```

The `/skills` slash command shows the same source annotation in its listing.

## Library API

```python
from chimera.skills.discovery import (
    discover_all_skills,        # native + (opt-in) foreign, merged
    discover_foreign_skills,    # foreign only, tagged by source
    resolve_foreign_config,     # (enabled, allowlist) from the config chain
    default_foreign_skill_dirs, # the well-known default allowlist
    format_skills_for_prompt,
)

# Honors the config chain (default off):
skills = discover_all_skills("/my/project")

# Force it on with an explicit allowlist (bypasses config):
skills = discover_all_skills(
    "/my/project",
    include_foreign=True,
    foreign_dirs=["~/.codex/skills"],
)

section = format_skills_for_prompt(skills)  # provenance-labeled
```

Every `Skill` carries a `source` field: `"chimera"` for a native skill, or the
configured directory string (e.g. `"~/.codex/skills"`) for a foreign one.
