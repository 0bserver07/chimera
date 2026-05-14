---
title: "Impact Analysis"
description: "Impact Analysis"
---

`chimera.training.impact` analyzes the blast radius of changing a
function or class.  `ImpactAnalyzer` walks the codebase using AST to find
callers, importers, and related tests for a given symbol.

---

## Key Classes

### ImpactReport

Dataclass summarising the impact of modifying a symbol.

| Field | Type | Description |
|-------|------|-------------|
| `symbol` | `str` | Name of the function/class being changed |
| `file_path` | `str` | File containing the symbol |
| `callers` | `list[CallerInfo]` | Functions that call the symbol |
| `importers` | `list[str]` | Files that import from this module |
| `tests` | `list[str]` | Test files that exercise this symbol |

Key method: `to_prompt_section() -> str` -- formats the report as a warning
for the agent prompt.

### ImpactAnalyzer

Walks all `.py` files under a working directory using AST analysis.
No code execution -- safe for any codebase.

Constructor: `ImpactAnalyzer(workdir)`.

Key method: `analyze(file_path, symbol_name) -> ImpactReport`.

## Usage

```python
from chimera.training.impact import ImpactAnalyzer

analyzer = ImpactAnalyzer(workdir="./src")
report = analyzer.analyze("utils.py", "parse_config")

print(f"Symbol: {report.symbol}")
print(f"Callers: {len(report.callers)}")
print(f"Importers: {len(report.importers)}")
print(f"Related tests: {len(report.tests)}")

# Inject into agent prompt
prompt = "Fix the parse_config function." + report.to_prompt_section()
result = agent.run(prompt, env=env)
```

## Related

- [Fault Localization](/fault-localization/) -- find where bugs are
- [Mutation Testing](/mutation-testing/) -- test suite quality analysis

<!-- W17-3 SOURCE FOOTER -->

## Source

`chimera/training/impact.py` -- verified against current source at wave-17.
