---
name: testgen
description: Generate test cases for a module or function based on source analysis
---

Generate comprehensive test cases for the specified code.

## Steps

1. **Identify the target.** If the user specifies a file or function, use that. Otherwise, find recently changed files with `git diff --name-only HEAD~3` and offer to generate tests for them.

2. **Analyze the source.** For each target file:
   - Read the full file to understand imports, classes, and functions
   - Identify public functions and methods (skip names starting with `_`)
   - Extract parameter types from annotations, docstrings, or usage patterns
   - Note return types and possible exceptions (look for `raise` statements)
   - Find existing test files using the project's test naming convention

3. **Generate three categories of tests for each function:**

   **Happy path tests:**
   - Call with typical, valid inputs
   - Verify the return value matches expected output
   - One test per distinct behavior branch

   **Edge case tests:**
   - Empty collections, zero values, None where allowed
   - Boundary values (max int, empty string, single-element list)
   - Unicode, special characters if the function processes strings

   **Error path tests:**
   - Invalid input types or values
   - Missing required arguments
   - Expected exceptions with `pytest.raises`

4. **Follow project conventions.** Before writing tests:
   - Check existing test files for patterns (fixtures, parametrize, class-based vs function-based)
   - Match the import style used in other tests
   - Use the same assertion style (assert vs self.assertEqual)

5. **Write the test file.** Place it according to the project's test layout. If a test file already exists for the target, add new tests rather than overwriting.

6. **Verify.** Run the new tests to confirm they pass: `pytest <test_file> -v`. Fix any failures before finishing.
