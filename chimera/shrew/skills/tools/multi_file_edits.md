---
name: multi-file-edits
description: How to structure changes that span more than one file without losing track.
triggers: ["refactor", "rename", "multiple files", "across files", "split file"]
---
## Structuring multi-file edits

When a single change spans two or more files, the small-model risk is
losing coherence between the edits — leaving a function renamed in one
file but called by the old name in another. The fix is to plan the
whole change before touching disk, then execute in a fixed order that
keeps the codebase compilable at every step.

The plan-first protocol:

1. **List every file that will change.** Either by inspection
   (knowing the codebase) or with a `grep` that finds all callers /
   importers of the symbol.
2. **Decide the edit order.** Keep imports valid as you go. Useful
   orders:
   - **Define-first.** Add the new function, class, or module first;
     then update callers; then remove the old definition.
   - **Inside-out.** Refactor the leaf first, work outward to the
     callers.
   - **Add-then-rename.** When renaming, add an alias to the old name
     so callers compile during the transition, then update callers,
     then remove the alias.
3. **Verify the plan with the user** if the change is large or
   touches public API. A 30-second confirmation beats reverting six
   files.
4. **Execute one file at a time.** After each `Edit`, briefly note
   what changed and what's left.
5. **Run the tests after the last file is patched, not between every
   edit.** Intermediate states will fail; that's expected.

Useful preflight commands:

- `grep -nR "old_name" --include='*.py' .` — find every call site for a
  rename.
- `grep -nR "from mod import" --include='*.py' .` — find every importer
  before splitting a module.
- `git status` — confirm you start clean. If the working tree already
  has unrelated changes, finish or stash those first.

When the change is genuinely large (10+ files), prefer to:

- Break it into 2–3 commits, each compilable on its own.
- Use the `replace_all` flag of `Edit` only when the symbol is
  globally unique and you've checked with `grep` first. A naive
  `replace_all` can rename a substring inside a docstring or a
  variable that happens to share a prefix.
- Stop and ask the user to review after the first file or two if the
  pattern isn't crisp yet — better to course-correct early.

The shape of a clean multi-file edit, in the assistant's voice:

> Plan: rename `parse_input` → `parse_request` across 4 files
> (`api.py`, `tests/test_api.py`, `cli.py`, `README.md`). Order:
> add alias in `api.py`, update callers, remove alias.
> Then run `pytest tests/test_api.py`.

Then execute. The plan in writing is the leash that keeps a small
model from drifting between edits.
