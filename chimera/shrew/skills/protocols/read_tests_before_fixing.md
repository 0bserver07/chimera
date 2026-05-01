---
name: read-tests-before-fixing
description: Read the test that's failing before reading the source it covers.
triggers: ["fix bug", "failing test", "test fails", "debug", "what does this test"]
---
## Read tests before fixing source

When a test fails, the small-model habit is to open the source file
and start hunting for "what looks wrong". This skips a much cheaper,
much more informative step: reading the test itself, slowly, in full.

**Why this order matters:**

- The test names the contract. The source is one implementation of
  that contract. If you patch the source without understanding the
  contract, you usually move the failure rather than fix it.
- The test fixtures (`pytest` markers, conftest, parametrize lists)
  often encode assumptions that are not in the source. Missing those
  assumptions is the single most common cause of "the patch passes my
  reproducer but the suite still fails".
- The traceback's "expected vs actual" line tells you the *shape* of
  the bug. That shape constrains the fix far more tightly than a
  generic read of the source ever will.

**The reading order:**

1. **Read the failing test function** end to end. Note its arguments,
   its setup, its assertions, and any fixtures it pulls in.
2. **Read the conftest** in the same directory (and any parent
   directories) for shared fixtures and parametrize markers.
3. **Read the test's imports.** They name the source modules under
   test. Now you know exactly which files matter.
4. **Run the test in isolation** with `-x -q` to confirm the failure
   matches what the user described.
5. **Only now, read the source** named in the imports — and only the
   parts the test actually exercises.

**Things to extract from the test:**

- What inputs does it pass? (Lists the supported input shapes.)
- What outputs does it expect? (Names the contract.)
- Does it assert exact equality or approximate / structural equality?
  (Tells you how strict the fix needs to be.)
- Does it patch / mock anything? (Tells you which collaborators are
  out of scope.)
- What's the *name* of the test? (Often a one-line spec of the
  behaviour.)

**Anti-patterns:**

- "Fixing" the test to match the broken source. The test is the
  spec. Touch source, not assertions, unless the test is provably
  wrong.
- Patching the function under test without re-reading the test
  fixtures. Half of small-model regressions in tested code come from
  ignoring a fixture-supplied default.
- Skipping the test once it goes green and assuming the bug is gone.
  Run the surrounding tests too — your fix may have flipped the
  failure into a different test.

The test is the cheapest source of truth in the repository. Read it
first, every time.
