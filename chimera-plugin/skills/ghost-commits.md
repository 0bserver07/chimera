---
name: ghost-commits
description: Create lightweight snapshots before every destructive operation so any change can be instantly reverted
triggers: ["undo", "revert", "checkpoint", "snapshot", "backup", "before editing", "safe"]
---

Before any operation that modifies files, create a snapshot so you can revert instantly if the change goes wrong. This is the difference between a 2-second undo and a 10-minute manual recovery.

## The Ghost Commit Protocol

1. **Before writing or editing any file,** capture its current state. In a git repo, the simplest approach:
   ```
   git stash push -m "ghost: before <description>" -- <file1> <file2>
   ```
   Or if you want to keep working tree changes visible:
   ```
   git add <files> && git commit -m "ghost: checkpoint before <description>"
   ```

2. **Label snapshots descriptively.** "ghost: before refactoring auth module" is useful. "ghost: checkpoint" is not. You may need to find the right snapshot later.

3. **Create snapshots at these moments:**
   - Before the first edit of a multi-file change
   - Before running a command that modifies files (code generation, formatting, migration)
   - Before deleting or moving files
   - Before applying a fix to a failing test (so you can revert if the fix causes regressions)

4. **Revert when things go wrong.** If your change breaks something:
   ```
   git checkout -- <file>           # revert a single file
   git stash pop                    # restore the stashed state
   git reset HEAD~1 --soft          # undo the ghost commit, keep changes staged
   ```

5. **Clean up after success.** Once your change is verified and committed properly, clean up ghost commits:
   - Drop stash entries: `git stash drop`
   - Ghost commits get squashed naturally when you make a real commit

## When NOT to Snapshot

- Reading files (no state change)
- Running tests (no file modification)
- Searching or grepping (no state change)
- When the file does not exist yet (creating a new file has no previous state)

## Why This Matters

- A failed refactor without a snapshot means manually reconstructing the previous state
- Ghost commits cost milliseconds to create and save minutes of recovery time
- They give you confidence to try bold changes — you can always undo
- In pair review, they provide a clear "before/after" for each logical change
