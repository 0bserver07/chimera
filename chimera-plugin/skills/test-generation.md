---
name: test-generation
description: Generate comprehensive tests from source analysis — find coverage gaps, create skeletons, rank candidates
triggers: ["test", "generate tests", "coverage", "untested", "missing tests", "test skeleton"]
---

When you need to write tests for a module, do not start from scratch. Use Chimera's test generation tools to analyze the source and produce a structured starting point.

## Step 1: Find Coverage Gaps

Before writing anything, identify what is untested. Call the `chimera_coverage_gaps` MCP tool with the source file path. It compares public functions in the source against `test_` functions in the corresponding test file and reports what is missing, with line numbers.

If it reports "All public functions have test coverage," you likely need deeper tests (edge cases, error paths), not more test functions.

## Step 2: Generate Test Skeletons

Call the `chimera_testgen` MCP tool with the source file path. It returns test stubs for every public function and method, categorized as:
- **unit** -- basic call with placeholder arguments
- **edge** -- edge-case inputs (empty, None)
- **error** -- error handling paths

These are skeletons, not finished tests. Each contains TODO comments marking where you need to fill in real arguments and assertions.

## Step 3: Fill In the Skeletons

For each generated skeleton:
1. Read the target function to understand its actual behavior
2. Replace placeholder arguments with realistic values
3. Replace `assert result is not None` with specific assertions about the expected output
4. For edge tests, use actual boundary values (empty strings, zero, negative numbers, None)
5. For error tests, assert that the correct exception is raised with `pytest.raises`

## Step 4: Run and Iterate

Run the tests after filling in each one. Do not fill in all skeletons and then run -- you will lose track of which test has which problem. Fix failures immediately before moving to the next skeleton.

## What NOT to Do

- Do not mock aggressively. If the function under test calls another function in the same module, let the real call happen unless it has side effects (network, disk, database).
- Do not write tests that pass by construction. `assert func(x) == func(x)` tests nothing.
- Do not skip the coverage gaps step. You may be duplicating tests that already exist.
