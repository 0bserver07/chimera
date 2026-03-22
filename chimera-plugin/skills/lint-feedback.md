---
name: lint-feedback
description: Run the linter after every edit and fix errors immediately, before moving to the next task
triggers: ["lint", "ruff", "flake8", "eslint", "style", "formatting", "type error"]
---

After every file edit, run the project's linter and fix any errors before moving on. Lint errors compound — fixing them later means re-reading code you have already forgotten.

## The Lint Feedback Loop

1. **Edit the file.** Make your change.

2. **Run the linter immediately.** Use the project's configured linter:
   - Python: `ruff check <file>` or `flake8 <file>` or `pylint <file>`
   - TypeScript/JavaScript: `eslint <file>` or `biome check <file>`
   - Rust: `cargo clippy`
   - Go: `golangci-lint run <file>`

   If you don't know the project's linter, check `pyproject.toml`, `package.json`, `.pre-commit-config.yaml`, or the CI config.

3. **Fix lint errors before proceeding.** For each error:
   - Read the error message carefully — it usually tells you exactly what to do
   - Fix the code, not the linter config (do not add `# noqa` or `// eslint-disable` unless the warning is genuinely wrong)
   - Re-run the linter on the same file to confirm the fix

4. **Run type checking too.** If the project uses type checking:
   - Python: `mypy <file>` or `pyright <file>`
   - TypeScript: the build step or `tsc --noEmit`
   Type errors often reveal real bugs, not just annotation issues.

5. **Cap the loop.** If you have fixed lint errors 3 times on the same file and new ones keep appearing, stop and re-read the file from the top. You may be making changes that conflict with each other.

## Why This Matters

- Lint errors in committed code break CI and block other contributors
- Type errors caught early prevent runtime crashes
- Fixing lint as you go takes 10 seconds; fixing a batch of 30 errors later takes 10 minutes
- The linter often catches real bugs: unused variables, unreachable code, wrong argument counts

## Common Pitfalls

- Do not disable linting rules project-wide to fix a local issue
- Do not reformat the entire file when you only changed 3 lines (causes noisy diffs)
- Do not ignore import-order warnings — they often indicate circular dependency risks
