---
title: "Context Mention"
description: "Context Mention"
---

`chimera.context.mentions` provides structured context injection via
`@`-mentions in user messages. The `MentionResolver` parses `@file:`,
`@folder:`, and `@url:` references, resolves their content, and returns
both a cleaned version of the text and the resolved `Mention` objects.

## Key Classes

| Class | Description |
|-------|-------------|
| `Mention` | A resolved mention with `type`, `reference`, and `content` fields |
| `MentionResolver` | Parses and resolves @-mentions from user text |

## Supported Mention Types

| Syntax | Description |
|--------|-------------|
| `@file:path` | Read a file's content |
| `@folder:path` | List directory tree (up to 50 entries) |
| `@url:https://...` | Fetch URL content (requires `httpx`) |

## Quick Start

```python
from chimera.context.mentions import MentionResolver

resolver = MentionResolver(workdir="/my/project")

text = "Review @file:utils.py and check @folder:tests/"
cleaned, mentions = resolver.resolve(text)

print(cleaned)  # "Review and check"
for m in mentions:
    print(f"{m.type}: {m.reference} -> {len(m.content)} chars")
```

## Inline Injection

`inject()` replaces mentions with their resolved content directly
in the message text:

```python
expanded = resolver.inject("Fix the bug in @file:main.py")
# Returns:
# Fix the bug in
# --- file: main.py ---
# <file content>
```

## Using with an Environment

Pass an `Environment` for workspace-aware file resolution:

```python
from chimera.env.local import LocalEnvironment
from chimera.context.mentions import MentionResolver

env = LocalEnvironment(workdir="/my/project")
resolver = MentionResolver(env=env)

cleaned, mentions = resolver.resolve("Explain @file:src/core.py")
```

## Import Reference

```python
from chimera.context.mentions import MentionResolver, Mention
```

## Related

- [Focus Chain](/focus-chain/) -- budget-aware context selection
- [History Processor](/history-processor/) -- conversation history transforms
- [Context-Aware Tools](/context-aware-tools/) -- tools with context access

<!-- W17-3 SOURCE FOOTER -->

## Source

`chimera/context/mentions.py` -- verified against current source at wave-17.
