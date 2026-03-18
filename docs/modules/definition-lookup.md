# Definition Lookup

`chimera.tools.definition_lookup` finds where functions, classes, methods,
and variables are defined across a codebase. It uses Python's `ast` module
for Python files and regex patterns for other languages (JS, TS, Go, Rust,
Java, Ruby, C, C++).

## Key Classes

| Class | Description |
|-------|-------------|
| `DefinitionLookupTool` | Agent tool that wraps `DefinitionFinder` |
| `DefinitionFinder` | Core search engine -- walks the file tree and matches definitions |
| `Definition` | Result dataclass with `symbol`, `kind`, `file`, `line`, and `source` |

## Quick Start

```python
from chimera.tools.definition_lookup import DefinitionFinder

finder = DefinitionFinder(workdir="/my/project")
defs = finder.find("MyClass")

for d in defs:
    print(f"{d.file}:{d.line} ({d.kind}) -- {d.source[:80]}")
```

## As an Agent Tool

`DefinitionLookupTool` is a `BaseTool` that agents can call during a
ReAct loop:

```python
from chimera.tools.definition_lookup import DefinitionLookupTool

tool = DefinitionLookupTool()
result = tool.execute({"symbol": "create_provider", "file_hint": "chimera/providers/factory.py"}, env=env)
print(result.output)
```

## How It Works

- **Python files**: Parsed with `ast` for accurate function, class, method,
  and variable definitions.
- **Other languages**: Matched with language-specific regex patterns covering
  JS/TS functions and classes, Go funcs and structs, Rust fn/struct/trait/enum,
  Java/C/C++ classes, and Ruby defs/modules.
- Directories like `.git`, `node_modules`, `__pycache__`, and `.venv` are
  automatically skipped.

## File Hint

Pass `file_hint` to search a specific file first, improving relevance
when you know approximately where a symbol lives:

```python
defs = finder.find("handle_request", file_hint="src/server.py")
```

## Import Reference

```python
from chimera.tools.definition_lookup import DefinitionLookupTool, DefinitionFinder, Definition
```

## Related

- [Tree-Sitter Parser](tree-sitter-parser.md) -- AST-based symbol extraction
- [Agent Tools](agent-tools.md) -- built-in tool catalogue
