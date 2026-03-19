---
title: "Sketch"
description: "Sketch"
---

`chimera.training.sketch` implements sketch synthesis -- filling holes in
partially-written source files.  A `SketchSpec` is built from source files
containing `# HOLE: <description>` markers.  The agent receives the full
file context but is instructed to fill only the marked holes.

---

## Key Classes

### Hole

A frozen dataclass representing a single hole in a sketch file.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `int` | Unique numeric identifier across all sketch files |
| `description` | `str` | Human-readable description from the marker |
| `file_path` | `str` | Path of the sketch file containing this hole |
| `line` | `int` | 1-based line number of the `# HOLE:` marker |
| `indent` | `str` | Leading whitespace of the marker line |

### SketchSpec

A `Spec` subclass created from source files with `# HOLE` markers.
Parses files for `# HOLE: <description>` comments and generates a prompt
instructing the agent to fill only the marked holes.

Constructors:

- `SketchSpec(files, description)` -- from a dict of `{path: content}`
- `SketchSpec.from_file(path)` -- load a single sketch file
- `SketchSpec.from_directory(path, pattern)` -- load all matching files

Key methods: `to_prompt()`, `write_sketches(env)`, property `holes`.

## Usage

```python
from chimera.training.sketch import SketchSpec

# Create a sketch with holes
sketch_code = '''
def fibonacci(n: int) -> int:
    # HOLE: implement fibonacci recursively
    pass

def factorial(n: int) -> int:
    # HOLE: implement factorial iteratively
    pass
'''

spec = SketchSpec(files={"math_utils.py": sketch_code})
print(f"Found {len(spec.holes)} holes to fill")

# Write sketches into the environment and synthesize
spec.write_sketches(env)
result = trainer.synthesize(spec=spec, strategy=TestConvergence())
```

## Related

- [Training concepts](/../concepts/training/) -- Spec and strategies
- `examples/sketch_synthesis.py` -- runnable example
