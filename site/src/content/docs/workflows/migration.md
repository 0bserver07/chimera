---
title: "Migration Workflow"
description: "Migration Workflow"
---

## What It Does

`MigrationPlanner` applies rule-based regex transformations to source files. Each `MigrationRule` defines a pattern, replacement, description, and file glob filter. Rules can be loaded from built-in presets (`python2-to-3`, `commonjs-to-esm`) or defined manually.

## CLI

```bash
chimera migrate --source ./src --preset python2-to-3
```

## Python API

Using a preset:

```python
from chimera.migration import MigrationPlanner

planner = MigrationPlanner.from_preset("python2-to-3")
files = {"app.py": 'print "hello"', "lib.py": "x = xrange(10)"}
result = planner.apply(files)
# result["app.py"] == 'print("hello")'
# result["lib.py"] == "x = range(10)"
```

Defining custom rules:

```python
from chimera.migration import MigrationPlanner, MigrationRule

planner = MigrationPlanner()
planner.add_rule(MigrationRule(
    pattern=r"assertEquals",
    replacement="assertEqual",
    description="Rename deprecated assertEquals to assertEqual",
    file_glob="*.py",
))

# Scan first to see what would change
scan_results = planner.scan(files)  # {filepath: [descriptions]}

# Generate a plan (only rules that match)
plan = planner.plan(files)

# Apply all rules
result = planner.apply(files)
```

## Key Classes

### `MigrationPlanner`

```python
class MigrationPlanner:
    def __init__(self) -> None
    def add_rule(self, rule: MigrationRule) -> None
    def scan(self, files: dict[str, str]) -> dict[str, list[str]]
    def plan(self, files: dict[str, str]) -> MigrationPlan
    def apply(self, files: dict[str, str]) -> dict[str, str]
    @classmethod
    def from_preset(cls, name: str) -> MigrationPlanner
```

Available presets: `"python2-to-3"` (print statements, raw_input, xrange), `"commonjs-to-esm"` (require/module.exports).

### `MigrationRule`

Dataclass with fields: `pattern` (regex str), `replacement` (str, supports backreferences), `description` (str), `file_glob` (str, default `"*"`).

**Methods:** `matches(source) -> list[str]`, `apply(source) -> str`.

### `MigrationPlan`

Dataclass with fields: `name`, `description`, `rules` (list of `MigrationRule`).

**Methods:** `validate(source) -> list[str]`, `apply(source) -> str`.

## Import

```python
from chimera.migration import MigrationPlanner, MigrationPlan, MigrationRule
```

<!-- W17-3 SOURCE FOOTER -->

## Source

`chimera/migration/` -- verified against current source at wave-17.
