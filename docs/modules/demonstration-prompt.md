# Demonstration Prompt

`chimera.core.demonstration` provides few-shot prompting via solved examples.
Inspired by SWE-Agent's demonstration system, `DemonstrationPrompt` includes
solved `(task, solution)` pairs in the prompt so the agent learns the expected
format and approach before tackling the actual task.

## Key Classes

| Class | Description |
|-------|-------------|
| `DemonstrationPrompt` | Prompt builder that renders system text + examples + task |
| `Example` | A solved example with `task`, `solution`, and `source` fields |

## Quick Start

```python
from chimera.core.demonstration import DemonstrationPrompt

demo = DemonstrationPrompt(
    system="You are a coding assistant.",
    max_examples=3,
)

demo.add_example(
    task="Write a function that reverses a string.",
    solution="def reverse(s: str) -> str:\n    return s[::-1]",
)

prompt_text = demo.render(task="Write a function that checks for palindromes.")
```

## Loading Examples from Files

Examples can be loaded from markdown files with `# Task` and `# Solution`
sections:

```python
# Load a single file
demo.add_from_file("examples/reverse_string.md")

# Load all .md files from a directory
demo.add_from_directory("examples/demonstrations/")
```

Expected file format:

```markdown
# Task
Write a function that reverses a string.

# Solution
def reverse(s: str) -> str:
    return s[::-1]
```

## Converting to a Prompt Object

`to_prompt()` converts the rendered demonstration into a Chimera `Prompt`
for direct use with an agent:

```python
prompt = demo.to_prompt()
agent = chimera.Agent(provider=provider, tools=tools, prompt=prompt)
```

## Import Reference

```python
from chimera.core.demonstration import DemonstrationPrompt, Example
```

## Related

- [Instruction Layer](instruction-layer.md) -- composable prompt layers
- [Agent Config](agents-config.md) -- agent configuration with presets
