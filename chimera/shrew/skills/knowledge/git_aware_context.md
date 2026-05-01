---
name: git-aware-context
description: Use git state as cheap, high-signal context before diving into source.
triggers: ["git", "diff", "branch", "uncommitted", "what changed", "recent changes"]
---
## Git-aware context for small models

The git working tree is a free context oracle most small models forget
to consult. A few seconds of `git status`, `git log`, and `git diff`
often save a hundred lines of speculative reading.

**Cheap commands that pay for themselves:**

- `git status -sb` — one line per modified file, plus the current branch
  and its upstream. Tells you immediately whether the tree is clean,
  whether the branch tracks a remote, and whether you are ahead/behind.
- `git log --oneline -n 10` — the last ten commit subjects. Names the
  recent themes of the project in a way no `ls` ever will.
- `git diff` — staged + unstaged changes. If the user said "fix the bug
  I introduced", this is almost always the right opening move before
  any other tool call.
- `git diff <ref>...HEAD -- <path>` — what changed on this branch in a
  specific path. Useful when the user mentions a feature without naming
  the files.
- `git blame -L <start>,<end> <file>` — who touched a region and when.
  Helps you understand whether a piece of code is recent (likely
  in-scope for the bug) or ancient (likely load-bearing).

**Things to check before you edit:**

- Is the working tree clean? If not, are the existing changes part of
  the task or unrelated drift you should not stomp on?
- What's the upstream branch? Pushing to `master` or `main` from a
  feature task is almost always wrong.
- Are there merge conflict markers (`<<<<<<<`) anywhere? If `grep -rn
  '^<<<<<<< ' .` returns hits, the tree is mid-merge and you should
  ask before editing.
- Has the file you're about to edit been touched recently by someone
  else? `git log -n 3 -- <file>` answers this in a token-cheap way.

**Things to avoid:**

- `git reset --hard`, `git checkout .`, `git clean -f` without the
  user's explicit instruction. These are destructive and silent.
- Committing on the user's behalf without being asked. Even when the
  diff is clean, the commit message and timing are the user's call.
- Force-pushing anything, ever, without an explicit request.

Read-only git commands cost almost nothing and give you context that
no other source provides. Make them the first call on any "fix"
or "review" task.
