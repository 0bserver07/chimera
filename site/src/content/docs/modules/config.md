---
title: "Config"
description: "Config"
---

Chimera's configuration system provides polymorphic serialization via `DiscriminatedUnion`, YAML/JSON file loading via `ChimeraConfig`, and project-level settings discovery via `ProjectConfig`. It also includes `Skill` / `SkillRegistry` for on-demand instruction sets and `StructuredOutput` for JSON schema validation of LLM responses.

## Quick Start

```python
from chimera.config import ChimeraConfig

config = ChimeraConfig.from_file("chimera.yaml")
env = config.create_environment()
strategy = config.create_strategy()
```

## Key Classes

| Class | Module | Description |
|-------|--------|-------------|
| `DiscriminatedUnion` | `chimera.config.union` | Base class for polymorphic config. `from_config(dict)` dispatches by `"type"` field; `to_config()` serializes back. Subclasses auto-register via `type_name` class variable. |
| `ChimeraConfig` | `chimera.config.config_file` | Loads a full Chimera configuration from YAML or JSON. Creates `Environment`, `Strategy`, and `CompactionStrategy` instances from their respective config sections. |
| `ProjectConfig` | `chimera.config.loader` | Discovers project-level rules files (`AGENTS.md`, `CLAUDE.md`, `.chimera/rules.md`) and skills directories. |
| `ConfigSource` | `chimera.config.loader` | Abstract base class for rule sources. `FileConfigSource` loads rules from a file. |
| `Skill` | `chimera.config.skills` | Dataclass representing an on-demand instruction set loaded from a `SKILL.md` file (`name`, `content`, `description`, `args`). |
| `SkillRegistry` | `chimera.config.skills` | Discovers and lazily loads `Skill` instances from directories containing `SKILL.md` files. |
| `StructuredOutput` | `chimera.config.structured` | JSON schema wrapper that validates LLM output. Parses JSON (including from markdown code blocks), checks required fields and types, and formats errors for retry. |

## Usage

### DiscriminatedUnion for polymorphic config

Define a union hierarchy by creating a base class with its own `_registry`, then subclass it with `type_name` set:

```python
from chimera.config import DiscriminatedUnion

class Transport(DiscriminatedUnion):
    _registry: dict[str, type] = {}

class HTTPTransport(Transport):
    type_name = "http"
    def __init__(self, url: str = "http://localhost"):
        self.url = url

class GRPCTransport(Transport):
    type_name = "grpc"
    def __init__(self, host: str = "localhost", port: int = 50051):
        self.host = host
        self.port = port

# Dispatch by "type" field
transport = Transport.from_config({"type": "http", "url": "https://api.example.com"})
# => HTTPTransport(url="https://api.example.com")

# Serialize back
transport.to_config()
# => {"type": "http", "url": "https://api.example.com"}

# List available types
Transport.available_types()  # => ["http", "grpc"]
```

### Loading project configuration

`ProjectConfig` scans a project directory for rules files and skills:

```python
from chimera.config import ProjectConfig

project = ProjectConfig.from_directory("./myapp")

# Concatenated rules text from AGENTS.md, CLAUDE.md, etc.
print(project.rules_text)

# Discover and load skills
print(project.skill_names)        # e.g. ["debugging", "refactoring"]
skill = project.get_skill("debugging")
if skill:
    print(skill.content)
```

### Structured output validation

Use `StructuredOutput` to validate that LLM responses match a JSON schema:

```python
from chimera.config import StructuredOutput

schema = StructuredOutput(
    name="code_review",
    schema={
        "type": "object",
        "required": ["issues", "score"],
        "properties": {
            "issues": {"type": "array"},
            "score": {"type": "integer"},
        },
    },
)

# Validate a response (also extracts JSON from ```code blocks```)
result = schema.validate('{"issues": ["unused import"], "score": 7}')
# => {"issues": ["unused import"], "score": 7}
```

## Persistent CLI defaults (Wave 11 A2)

`chimera.cli.config_cmd` writes a separate, simpler TOML file at
`~/.chimera/config.toml` for CLI-only defaults. Keys are
dot-namespaced (`otter.model`, `global.no-color`, `shrew.vram-gb`);
bare keys auto-bucket to `[global]`.

```bash
chimera config set otter.model claude-sonnet-4-6
chimera config get otter.model
chimera config list --cli otter
chimera config unset otter.model
chimera config edit                 # opens $EDITOR
```

Subcommands opt in to the new defaults via the
`chimera.cli.config_loader.resolve_default()` helper:

```python
from chimera.cli.config_loader import resolve_default

model = resolve_default("otter", "model", fallback="claude-sonnet-4-6")
```

Resolution order: explicit CLI flag → environment variable → TOML
default → fallback. The `--cli` filter on `list` accepts any of
`global`, `mink`, `otter`, `ferret`, `weasel`, `shrew`, `stoat`,
`badger`. Stdlib-only — no `tomli` dependency on Python 3.11+.

This file is *separate* from `ChimeraConfig` (project-scoped YAML /
JSON for environments, strategies, compaction). The two never
overlap.

## Integration

- **DiscriminatedUnion** is the foundation for polymorphic config throughout Chimera. `Environment`, `Strategy`, and `CompactionStrategy` all extend it, so `ChimeraConfig` can instantiate any registered subclass from a YAML/JSON `"type"` field.
- **ChimeraConfig** bridges config files to runtime objects:
    - `create_environment()` dispatches through `Environment.from_config()`
    - `create_strategy()` dispatches through `Strategy.from_config()`
    - `create_compaction()` dispatches through `CompactionStrategy.from_config()`
- **ProjectConfig** feeds discovered rules into the system prompt via `rules_text`, and skills into the `/skill` REPL command.
- **SkillRegistry** uses lazy loading -- skills are only read from disk when first accessed by name.
- **StructuredOutput** is used by agents and workflows to enforce typed responses from LLMs, with automatic retry on validation failure (up to `max_retries`).
- **Persistent CLI defaults** (`~/.chimera/config.toml`): consumed by `chimera config` and `resolve_default()`. The marketplace (`chimera plugins`) reads `[global] plugin_index` from the same file when neither `--index` nor `$CHIMERA_PLUGIN_INDEX` resolves.

## Import Reference

```python
from chimera.config import (
    ChimeraConfig,
    ConfigSource,
    DiscriminatedUnion,
    ProjectConfig,
    Skill,
    SkillRegistry,
    StructuredOutput,
)
```
