---
title: Weasel Themes
description: Theme registry for chimera weasel — color palettes and REPL prompt-prefix bundles. Built-in themes plus on-disk JSON discovery.
---

# Weasel Themes

Themes are a core extension surface for `chimera weasel`. A theme bundles
two small mappings under one named identifier:

- **`colors`** — palette slots (`foreground`, `background`, `accent`,
  `muted`, …) keyed to color values. Renderers can splice these into
  ANSI / true-color escape sequences as needed.
- **`style_prompts`** — REPL prompt-prefix slots (`user`, `assistant`,
  `tool`, `error`, …) keyed to the literal strings the REPL prints in
  front of each output segment.

Three themes ship in the box: `default`, `dark`, and `solarized`. Drop
additional JSON files under `~/.weasel/themes/` (user scope) or
`<project>/.weasel/themes/` (project scope) to register more — project
scope wins on name collision.

## Selecting a theme

```bash
chimera weasel --theme dark
chimera weasel --theme solarized -p "explain this repo"
WEASEL_THEME=solarized chimera weasel
```

Unknown names fall back to `default` rather than erroring, and bad JSON
is silently skipped — a single malformed theme cannot break a weasel
invocation.

## File shape

```json
{
  "name": "midnight",
  "colors": {
    "foreground": "#e6e6e6",
    "background": "#0b0b1a",
    "accent": "#6cb6ff",
    "muted": "#7a7a8c",
    "error": "#ff5f5f"
  },
  "style_prompts": {
    "user": "you> ",
    "assistant": "",
    "tool": "[tool] ",
    "error": "[error] "
  }
}
```

All fields are optional. When `name` is omitted the file's stem is used
as the theme identifier. Unknown color / prompt slots are preserved
verbatim so embedders can stash brand-specific keys without subclassing.

## Discovery roots

Walked in this order, with later entries overriding earlier ones on
name collision:

1. **Built-ins:** `default`, `dark`, `solarized`.
2. **User scope:** `~/.weasel/themes/*.json`.
3. **Project scope:** `<project_root>/.weasel/themes/*.json`.

Hidden files (`.foo.json`) and non-`.json` files are skipped.

## Built-in palettes

| Name | Foreground | Background | Accent |
| --- | --- | --- | --- |
| `default` | `#d0d0d0` | `#1c1c1c` | `#5fafff` |
| `dark` | `#f5f5f5` | `#0a0a0a` | `#00d7d7` |
| `solarized` | `#839496` | `#002b36` | `#268bd2` |

Each ships with `error` and `muted` slots populated and a `style_prompts`
mapping at minimum covering the `user` REPL prompt prefix.

## Programmatic API

```python
from pathlib import Path

from chimera.weasel.themes import (
    Theme,
    get_theme,
    load_themes,
)

# One-shot lookup against the module-level built-in cache:
default = get_theme("default")
dark = get_theme("dark")

# Discover everything under both scope roots:
registry = load_themes(Path.cwd())
solarized = registry["solarized"]
print(solarized.colors["accent"])
print(solarized.style_prompts["user"])

# Pass a registry to get_theme to honor on-disk overrides:
theme = get_theme("midnight", registry=registry)
```

`Theme` is a stdlib `dataclass`. The whole module is dependency-free,
so embedders can pull it in without importing the rest of chimera.
