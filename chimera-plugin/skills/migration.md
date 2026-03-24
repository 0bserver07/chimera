---
name: migration
description: Plan and apply codebase migrations using rule-based transforms — scan for opportunities, preview changes, apply atomically
triggers: ["migrate", "migration", "upgrade", "convert", "python2", "commonjs", "esm", "refactor pattern"]
---

When migrating code between language versions or module systems, use Chimera's migration tools instead of doing find-and-replace by hand.

## Step 1: Check Available Presets

Call the `chimera_migration_presets` MCP tool to see what built-in migrations are available. Currently supported:
- **python2-to-3** -- print statements, raw_input, xrange (4 rules)
- **commonjs-to-esm** -- require() to import, module.exports to export default (2 rules)

If none of the presets match your needs, you will need to define custom rules (see step 4).

## Step 2: Scan Before Applying

Call `chimera_migration_scan` with the file contents and preset name. This shows you what will change without modifying anything. Review the results:
- How many files are affected?
- Which rules matched?
- Are there any false positives (patterns that match but should not be changed)?

If the scan shows false positives, do not use the preset blindly. Apply it to safe files and handle the edge cases manually.

## Step 3: Apply the Migration

Call `chimera_migration_apply` with the same files and preset. It returns the transformed file contents. Write the transformed content back to the files.

After applying, immediately:
1. Run the linter to catch syntax issues introduced by the transforms
2. Run the test suite to verify nothing broke
3. Review the diff to confirm the changes look correct

## Step 4: Custom Migrations

For migrations not covered by a preset, define rules as a `MigrationRule` with:
- `pattern` -- regex to match (supports capture groups)
- `replacement` -- replacement string (use `\1`, `\2` for backreferences)
- `description` -- what the rule does (shown in scan output)
- `file_glob` -- which files to apply to (e.g., `"*.py"`, `"*.js"`)

Build a planner, add rules, scan, review, then apply.

## Common Pitfalls

- Do not apply a migration without scanning first. Regex rules can match in unexpected places (strings, comments, unrelated code).
- Do not apply multiple presets to the same files in one pass without checking for conflicts.
- Always run tests after applying. Regex transforms do not understand semantics -- they can produce syntactically valid but logically wrong code.
