---
title: "chimera.config"
description: "Reference for chimera.config — discriminated unions, YAML/JSON config files, project discovery, structured config."
---

`chimera.config` loads and validates user / project configuration. The
core abstraction is the discriminated union — every type field selects
which subclass to deserialise into.

## Top-level exports

```python
from chimera.config import (
    DiscriminatedUnion,
    ChimeraConfig,
    ProjectConfig,
)
```

| Symbol | Module | Purpose |
|---|---|---|
| `DiscriminatedUnion` | `chimera.config.union` | Base class. `from_config(d)` dispatches on a `type` field; `to_config()` round-trips back to a dict. |
| `ChimeraConfig` | `chimera.config.config_file` | YAML / JSON config-file loader. |
| `ProjectConfig` | `chimera.config.loader` | Project-config discovery (walks up from cwd looking for `chimera.toml` / `.chimera/config.yml`). |

The `chimera.config.skills` and `chimera.config.structured` submodules
hold smaller helpers used by the loader; their exports are stable but
rarely imported directly.

## See also

- [`chimera.plugins`](/reference/plugins/) for plugin-config layering.
