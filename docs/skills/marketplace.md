# Skills marketplace — remote index download

`chimera.skills.discovery` ships with a tiny remote-index helper: point
it at a URL hosting an `index.json` manifest and it downloads every
listed `SKILL.md` into `~/.chimera/cache/skills/`.

## Manifest schema

```json
{
  "skills": [
    {
      "name": "binary-search",
      "description": "Iterative binary search cheat-sheet",
      "url": "https://example.com/skills/binary-search/SKILL.md",
      "version": "1.0.0"
    }
  ]
}
```

A bare list (`[{...}, ...]`) is also accepted so a flat manifest works
with no envelope. Entries are filtered to those that:

1. Have non-empty `name` and `url`.
2. Have a `name` matching `[a-z0-9][a-z0-9-]{0,63}` (the same shape as
   local SKILL.md frontmatter).
3. Use an `http` or `https` scheme — `file://` and other URLs are
   silently dropped to keep arbitrary filesystem reads off the table.

## CLI

```bash
chimera otter skills fetch https://example.com/skills/index.json \
  [--skills-cache PATH] \
  [--skills-overwrite]
```

Default cache: `~/.chimera/cache/skills/`. Each manifest entry produces
`<cache>/<name>/SKILL.md`. Already-cached entries are skipped unless
`--skills-overwrite` is set.

## Library API

```python
from chimera.skills.discovery import (
    download_remote_skills,
    fetch_remote_index,
    default_remote_cache,
)

# Just inspect the manifest:
entries = fetch_remote_index("https://example.com/index.json")
for e in entries:
    print(e["name"], e["description"])

# Download + return a Skill list (matches discover_skills() shape):
skills = download_remote_skills(
    "https://example.com/index.json",
    cache_dir=default_remote_cache(),
    overwrite=False,
)
```

## How it works

`download_remote_skills` walks the parsed manifest, downloads each
`SKILL.md` via stdlib `urllib`, and writes it under
`<cache>/<name>/SKILL.md`. If the downloaded body lacks YAML
frontmatter, the manifest's `name` / `description` are padded onto the
front so the file passes `discover_skills()` validation. Individual
download failures are skipped so one broken entry doesn't poison the
batch — the function returns the freshly-cached `Skill` instances.

The cache root nests under `~/.chimera/cache/` so a misbehaving cache
can be wiped with `rm -rf ~/.chimera/cache`.

## Trademark hygiene

`chimera.skills.discovery` is provider-neutral — no upstream brand
appears in source or docs. The trademark scrubs in
`scripts/*_trademark_scrub.sh` cover the file.
