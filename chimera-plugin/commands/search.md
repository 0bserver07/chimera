---
name: search
description: Deep codebase search — find definitions, usages, call chains, and architectural patterns
---

Perform a thorough codebase search that goes beyond simple text matching.

## Steps

1. **Clarify the query.** Determine what the user is looking for:
   - A function/class definition? Search for `def <name>` or `class <name>`
   - All usages of a symbol? Search for the name and filter out the definition
   - A concept or pattern? Use multiple search terms
   - A call chain? Trace caller-to-callee relationships

2. **Search broadly first.** Start with a wide search to understand scope:
   - Use `Grep` with the primary term across the whole codebase
   - Note which directories and files contain matches
   - If too many results, narrow by file type or directory

3. **Follow the dependency graph.** For each key result:
   - Read the file to understand the full context (not just the matching line)
   - Check imports to find where the symbol is defined
   - Check what imports the defining module to find all consumers
   - Trace through at most 3 levels of indirection

4. **Build a map.** Present findings as a structured overview:
   ```
   Symbol: create_provider
   Defined in: chimera/providers/factory.py:15
   Used by:
     - chimera/core/agent.py:42 (Agent.__init__)
     - chimera/cli/code.py:88 (REPL setup)
     - tests/test_factory.py:12 (unit tests)
   Calls into:
     - AnthropicProvider.__init__
     - OpenAIProvider.__init__
   ```

5. **Answer the underlying question.** Don't just dump search results. Synthesize what you found into a clear answer about how the code works, where something is defined, or why it's structured that way.
