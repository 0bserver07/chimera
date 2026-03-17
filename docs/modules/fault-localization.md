# Fault Localization

`chimera.training.fault_localization` ranks code locations by suspiciousness
using test failure analysis.  It parses pytest output, extracts traceback
references, and scores locations that appear more frequently in failing tests
using an Ochiai-inspired approach.

---

## Key Classes

### SuspiciousLocation

Dataclass representing a code location ranked by suspiciousness.

| Field | Type | Description |
|-------|------|-------------|
| `file` | `str` | Source file path |
| `function` | `str \| None` | Enclosing function name (if identified) |
| `line` | `int` | Line number in the source file |
| `score` | `float` | 0.0 (not suspicious) to 1.0 (very suspicious) |
| `reason` | `str` | Human-readable explanation of the score |

### FaultLocalizer

Parses test output and ranks suspicious locations.  Filters out test files
so only production code is reported.

Key methods:

- `localize(test_output) -> list[SuspiciousLocation]` -- parse and rank
- `augment_prompt(prompt, locations, max_locations) -> str` -- append
  suspected bug locations to an agent prompt

## Usage

```python
from chimera.training.fault_localization import FaultLocalizer

localizer = FaultLocalizer()

# Parse pytest output
test_output = env.run_tests().output
locations = localizer.localize(test_output)

for loc in locations[:3]:
    print(f"{loc.file}:{loc.line} — {loc.score:.0%} suspicious")
    print(f"  {loc.reason}")

# Augment an agent prompt with fault info
prompt = localizer.augment_prompt(
    "Fix the failing tests in the calculator module.",
    locations,
    max_locations=3,
)
result = agent.run(prompt, env=env)
```

## Related

- [Impact Analysis](impact-analysis.md) -- analyze blast radius of changes
- [Training concepts](../concepts/training.md) -- strategies that use test feedback
