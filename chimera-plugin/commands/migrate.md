---
name: migrate
description: Plan and execute a codebase migration with rule-based transforms
---

Plan and execute a structured code migration.

## Steps

1. **Identify the migration.** Ask the user what migration to perform. Common migrations:
   - Python 2 to 3 (print statements, dict methods, unicode)
   - CommonJS to ESM (require/module.exports to import/export)
   - Class components to hooks (React)
   - Callback-based to async/await
   - Framework version upgrades (detect deprecated APIs)

2. **Scan the codebase.** Find all files that need changes:
   - Use `grep` / `Grep` to find patterns that match the migration rules
   - Count occurrences per file to estimate scope
   - Present a summary: "Found N occurrences across M files"

3. **Create a migration plan.** Before making any changes, list:
   - Every file that will be modified
   - What pattern will be replaced and with what
   - Any files that need manual review (complex cases the rules cannot handle)

4. **Apply transforms file by file.** For each file:
   - Read the current content
   - Apply all applicable rules
   - Write the updated content
   - Verify syntax is valid (run the language's parser/compiler on the file)

5. **Handle edge cases.** Flag anything that cannot be auto-migrated:
   - Dynamic patterns (e.g., `getattr` with variable names)
   - Conditional imports or platform-specific code
   - Generated code or vendored dependencies (skip these)

6. **Verify.** After all transforms:
   - Run the test suite to catch regressions
   - Run the linter to catch new style issues
   - Present a summary of changes made and any manual follow-ups needed

Always create a git checkpoint before starting: `git stash` or commit current work so the migration can be reverted cleanly.
