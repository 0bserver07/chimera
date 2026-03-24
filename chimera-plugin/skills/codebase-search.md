---
name: codebase-search
description: Search the codebase semantically using TF-IDF ranking and symbol lookup
triggers: ["search", "find", "where is", "definition of", "who uses", "file not found"]
---

Use Chimera's CodebaseIndex for semantic search when basic grep is not enough.

## When to Use This

- Searching for a concept (not an exact string)
- Finding all definitions of a symbol across languages
- Validating that file paths exist before editing
- Locating files related to a feature when you do not know the directory structure

## How to Search

1. **Semantic search:** Call the `chimera_search` MCP tool with a natural language query. It returns files ranked by TF-IDF relevance. Use this when you know what you want conceptually but not which file contains it.

2. **Symbol lookup:** Call the `chimera_symbols` MCP tool with a class or function name. It returns definitions across Python, TypeScript, Go, and Rust with file paths, line numbers, and source snippets. Use this when you know the symbol name.

3. **Path validation:** The `validate_path` hook automatically checks that files exist before Write/Edit. If blocked, check the suggested paths in the error message -- you likely have a typo or the file was renamed.

## What to Do With Results

- Read the top 3-5 results to understand context before making changes.
- For symbol lookup, trace imports from the definition to find all consumers.
- If `validate_path` blocks your edit, do not guess a new path. Use `chimera_search` or `chimera_symbols` to find the correct file, then retry.
- If search returns no results, try different terms: use the concept name rather than the exact identifier, or break a compound term into separate words.

## Fallback

If the Chimera MCP server is not available, fall back to Grep with multiple search strategies:
1. Search for the exact identifier.
2. Search for the filename component of the path.
3. Search for related terms (synonyms, abbreviations).
