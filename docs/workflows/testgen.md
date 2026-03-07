# Test Generation Workflow

## What It Does

`TestGenerator` analyzes Python source files using AST parsing and generates pytest-structured test case skeletons. For each public function it produces a basic unit test, an edge-case test (when parameters exist), and an error-handling test. For public class methods it generates a unit test that instantiates the class.

## CLI

```bash
chimera testgen --source ./chimera --output tests/generated
```

## Python API

```python
from chimera.testgen import TestGenerator, TestCase

generator = TestGenerator()

# Analyze a single file
cases = generator.analyze("chimera/core/agent.py")
for case in cases:
    print(f"{case.name} ({case.category}): {case.target_function}")
    print(case.test_code)
```

Analyze from source string directly:

```python
source = '''
def add(a, b):
    return a + b
'''
cases = generator.analyze_source(source, filepath="math_utils.py")
# Generates: test_add (unit), test_add_edge_empty (edge), test_add_error (error)
```

Access all accumulated test cases:

```python
all_cases = generator.test_cases
generator.clear()  # reset accumulated cases
```

## Key Classes

### `TestGenerator`

```python
class TestGenerator:
    def __init__(self) -> None
    def analyze(self, filepath: str) -> list[TestCase]
    def analyze_source(self, source: str, filepath: str = "<unknown>") -> list[TestCase]
    def clear(self) -> None
```

**Properties:** `test_cases` (list of all accumulated `TestCase` instances).

### `TestCase`

Dataclass with fields: `name` (str), `target_function` (str), `target_file` (str), `test_code` (str), `category` (str: `"unit"`, `"edge"`, or `"error"`).

### `CoverageReport`

Dataclass with fields: `total_statements`, `total_missing`, `coverage_percent`, `file_coverage` (dict mapping filepath to percent), `uncovered_lines` (dict mapping filepath to line numbers).

**Properties:** `covered_statements`.
**Methods:** `files_below(threshold) -> list[str]`.

### `parse_coverage(output: str) -> CoverageReport`

Parses pytest-cov / coverage.py text output into a `CoverageReport`.

## Import

```python
from chimera.testgen import TestGenerator, TestCase, CoverageReport, parse_coverage
```
