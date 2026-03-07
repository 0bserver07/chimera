# Doc Generation Workflow

## What It Does

`DocGenerator` scans Python source files using AST parsing, extracts classes, functions, and docstrings into `DocSection` entries, and writes markdown documentation files. It generates per-module pages and an index page linking them together.

## CLI

```bash
chimera docs --source ./chimera --output docs/api
```

## Python API

```python
from chimera.docs import DocGenerator, DocSection

generator = DocGenerator(root="./chimera", output_dir="docs/api")

# Scan source tree and extract documentation
sections = generator.scan(extensions=(".py",))
print(f"Found {len(sections)} documented modules")

# Write markdown files
written = generator.write(sections)
print(f"Wrote {len(written)} files")
```

`DocSection` objects form a tree and can be rendered directly:

```python
for section in generator.sections:
    print(section.to_markdown(level=1))
```

## Key Classes

### `DocGenerator`

```python
class DocGenerator:
    def __init__(self, root: str, output_dir: str = "docs/api") -> None
    def scan(self, extensions: tuple[str, ...] = (".py",)) -> list[DocSection]
    def write(self, sections: list[DocSection] | None = None) -> list[str]
```

**Properties:** `sections` (list of `DocSection`).

`scan()` walks the source tree, skips hidden directories, `__pycache__`, `node_modules`, `venv`, and `.git`. For each Python file, it parses the AST and extracts module docstrings, class definitions with method signatures, and standalone functions.

`write()` creates one markdown file per module plus an `index.md` in the output directory. Returns the list of written file paths.

### `DocSection`

Dataclass with fields: `title` (str), `content` (str), `subsections` (list of `DocSection`), `source_file` (str).

**Methods:** `to_markdown(level=1) -> str` -- renders the section and its subsections as markdown with appropriate heading levels.

## Import

```python
from chimera.docs import DocGenerator, DocSection
```
