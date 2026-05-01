---
name: find-vs-grep-vs-rg
description: Pick the right search tool for the question — find, grep, or ripgrep.
triggers: ["find", "grep", "rg", "ripgrep", "search files", "search content"]
---
## Find vs grep vs rg

Three tools, three jobs. Small models often pick by habit instead of
by question, which is how a "where is this defined?" answer ends up
costing five tool calls.

**`find` — answers questions about files.**

Use `find` when the question is about file metadata: name, path,
size, modification time, type. It does not look inside files.

- `find . -name '*.py'` — every Python file under the cwd.
- `find . -type f -newer reference.txt` — files modified after
  `reference.txt`.
- `find . -size +1M` — files larger than 1 MiB.
- `find . -path '*/tests/*' -name 'test_*.py'` — tests that match a
  shape.

`find` is also the right tool for "I need to act on each match"
because it composes with `-exec` or `-print0 | xargs -0`.

**`grep` — answers questions about content, in a portable way.**

Use `grep` when you need to look *inside* files for a pattern, and
you want broad portability (it ships with every Unix). It is slower
than `rg` on large trees but always available.

- `grep -nR 'def parse_args' .` — line-numbered, recursive,
  case-sensitive search for the literal string.
- `grep -nR --include='*.py' 'TODO' .` — restrict by file glob.
- `grep -nE 'foo|bar' file` — extended regex.
- `grep -l pattern -r .` — list filenames that match, not the lines.
- `grep -B 2 -A 2 pattern file` — show 2 lines of context on each
  side of the match.

**`rg` (ripgrep) — answers content questions, fast.**

When `rg` is installed (almost always on a developer machine, often
not in CI containers), prefer it over `grep` for large repos. It
respects `.gitignore` by default, which means you don't waste time
crawling `node_modules/` or `.venv/`.

- `rg 'pattern'` — recursive by default, line-numbered, ignores
  binary files and `.gitignore`-listed paths.
- `rg -t py 'pattern'` — restrict by file type without remembering
  the glob.
- `rg -l 'pattern'` — filenames only.
- `rg -e 'foo' -e 'bar'` — multiple patterns.
- `rg --files | rg 'test_'` — list files matching a name pattern,
  using `rg`'s file-listing mode.

**Decision shortcuts:**

- "Where is this function defined?" → `rg 'def name\b'` (or `grep`
  fallback).
- "What files are there?" → `find` for arbitrary trees, `rg --files`
  inside a git repo.
- "Which files import X?" → `rg "from X import|import X"`.
- "Which files were touched recently?" → `find . -mtime -1` or `git
  log --since=yesterday --name-only`.

**Anti-patterns:**

- `find . -name '*.py' | xargs grep pattern` when `rg pattern -t py`
  exists. Same answer, one process, no `xargs` quoting bugs.
- `grep -R` against `node_modules/` or `target/`. You will regret
  the wait. `rg` skips them automatically.
- `find /` from the wrong directory. Slow, scary, and almost
  certainly the wrong question.

Pick by question type, not by reflex. Right tool, fewer calls.
