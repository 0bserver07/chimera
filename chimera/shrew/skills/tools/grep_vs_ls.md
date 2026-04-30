---
name: grep-vs-ls
description: When to reach for grep over ls (and vice versa) when exploring a repo.
triggers: ["find file", "where is", "list directory", "search code", "grep", "ls"]
---
## Grep vs ls — pick the right exploration tool

Small models often default to `ls -R` and dump the entire tree into
context, which costs a lot of tokens and tells the model little about
what's actually inside the files. `grep` (or the project's `Grep`
tool) is usually the right exploration primitive instead.

**Reach for `grep` / `Grep` when:**

- You know the symbol (function, class, variable, route, error message)
  but not the file. `grep -nR "def parse_args" .` or the equivalent
  `Grep` call resolves it in one tool call.
- You're looking for usages of an API to see how it's called.
- You're hunting for a string the user pasted (an error message, a log
  line). Search for a unique substring rather than the whole quote.
- You want a quick map of "what does this codebase even talk about" —
  `grep -n "^class " --include='*.py' -r .` lists every class name.

Useful flags worth remembering:

- `-n` — line numbers. Always include these when sharing matches with
  the user.
- `-r` — recurse into directories.
- `--include='*.py'` — restrict by file glob; cheap and effective.
- `-w` — match whole words; fewer false positives on short symbols.
- `-l` — list matching files only, not the lines. Good for locating a
  module before reading it.

**Reach for `ls` (or `list_files`) when:**

- You actually need the directory layout — what subpackages exist,
  what config files sit at the root, whether a `tests/` directory is
  present.
- You're checking whether a file or directory exists before a `Write`
  or `mkdir`.
- The user is asking about project structure, not behaviour.

**Avoid:**

- `ls -R` on an unfamiliar repo's root. Modern projects have
  `node_modules/`, `.venv/`, `dist/`, `target/` — the recursive listing
  is mostly noise. If you must, exclude these explicitly.
- `find /` style searches. They're slow, blow up context, and almost
  always answer the wrong question.

A useful default: when the user asks "where is X?", try `grep` first
with the most specific identifier you can extract from the question.
Fall back to `ls` only when the identifier is genuinely about the
filesystem rather than the code.
