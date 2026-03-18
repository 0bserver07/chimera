# Focus Chain

`chimera.context.focus` provides token-budget-aware context selection.
Instead of sending everything to the LLM, `FocusChain` ranks context items
by relevance and selects the most important ones that fit within a
configurable token budget.

## Key Classes

| Class | Description |
|-------|-------------|
| `ContextItem` | A piece of context with `content`, `source`, `tokens`, and `relevance` fields |
| `FocusChain` | Selects the highest-relevance items that fit within a token budget |

## Quick Start

```python
from chimera.context.focus import FocusChain

chain = FocusChain(token_budget=4000)

chain.add("def hello(): ...", source="file:utils.py", relevance=0.9)
chain.add("README content...", source="file:README.md", relevance=0.3)
chain.add("Test fixtures...", source="file:conftest.py", relevance=0.7)

# Select items that fit the budget, highest relevance first
selected = chain.select()
for item in selected:
    print(f"{item.source} (relevance={item.relevance}, tokens={item.tokens})")
```

## Adding Files from an Environment

```python
from chimera.context.focus import FocusChain
from chimera.env.local import LocalEnvironment

env = LocalEnvironment(workdir="/my/project")
chain = FocusChain(token_budget=8000)

chain.add_file("src/main.py", env, relevance=1.0)
chain.add_files(["tests/test_main.py", "pyproject.toml"], env, relevance=0.5)
```

## Rendering as a Prompt Section

`to_prompt_section()` renders the selected items as a Markdown block
ready to inject into a system prompt:

```python
section = chain.to_prompt_section()
# Returns:
# ## Context
# ### file:utils.py
# def hello(): ...
```

## API Summary

| Method | Description |
|--------|-------------|
| `add(content, source, relevance)` | Add a text item with estimated token count |
| `add_file(path, env, relevance)` | Add a file's content from an environment |
| `add_files(paths, env, relevance)` | Add multiple files |
| `select()` | Return items fitting the budget, sorted by relevance |
| `to_prompt_section()` | Render selected items as Markdown |
| `clear()` | Remove all items |

## Related

- [Compaction](compaction.md) -- token-level history compression
- [Context Mention](context-mention.md) -- @-mention context injection
- [History Processor](history-processor.md) -- conversation history transforms
