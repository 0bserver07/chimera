---
name: auto-test-lint
description: Tests and lint run automatically after every file edit -- fix failures immediately before moving on
triggers: ["test", "lint", "broke", "failing", "regression", "ruff", "pytest", "edit"]
---

After every Write or Edit, two hooks fire automatically: one runs related tests, the other runs the linter. You do not need to invoke them yourself -- they are PostToolUse hooks. Your job is to read their output and act on it.

## What Happens Automatically

1. **auto_test.py** finds the test file related to your edit (convention: `foo.py` -> `tests/test_foo.py`, or co-located `test_foo.py`, or content-search fallback). It runs `pytest --tb=short -q` on those files and prints results. You will see either `[auto-test] PASSED` or `[auto-test] FAILED` with failure details.

2. **auto_lint.py** runs the appropriate linter for the file type (Python: `ruff check`, JS/TS: `eslint`, Rust: `cargo clippy`, Go: `golangci-lint`). You will see either `[auto-lint] Lint clean` or `[auto-lint] Issues found` with the linter output.

3. **security_scan.py** runs before every Bash command. If you try to run something dangerous (like `rm -rf /`, `chmod 777`, piping curl to sh), it blocks the command with exit code 2 and explains why. You do not need to worry about this -- just do not try to circumvent the block.

## What You Must Do

1. **Read the test output after every edit.** If tests failed, fix the failure before making any other changes. Do not stack edits on top of broken tests.

2. **Read the lint output after every edit.** If the linter reported issues, fix them immediately. Common fixes:
   - Unused imports: remove them
   - Missing type annotations: add them
   - Line too long: break the line
   - Naming violations: rename to match project convention

3. **Do not suppress warnings to make them go away.** Do not add `# noqa`, `# type: ignore`, or `// eslint-disable` unless the warning is genuinely incorrect. Fix the code instead.

4. **Do not declare done until verify_done passes.** When you try to stop, the `verify_done.py` hook runs the full test suite. If it fails (exit 1), you are not done. Read the failure output and keep fixing.

## If No Tests Are Found

If you see `[auto-test] No related tests found for foo.py`, this means:
- There is no `tests/test_foo.py` file
- There is no co-located `test_foo.py`
- No test file in `tests/` mentions the module name

This does not mean your edit is safe. Consider writing a test if you are making a non-trivial change to code that lacks test coverage.

## If the Linter Is Not Installed

If you see `Linter not found: ruff`, the linter binary is not available. Note this and move on -- do not install linters as part of your task unless explicitly asked.

## Retry Protocol for Test Failures

If a test fails after your edit:
1. Read the full failure output, including the traceback
2. Identify whether the failure is in your changed code or pre-existing
3. If your change caused it, fix the root cause (not the test assertion)
4. Re-edit the file -- auto_test will run again automatically
5. After three failed attempts on the same test, re-read the test setup and the function under test from scratch
