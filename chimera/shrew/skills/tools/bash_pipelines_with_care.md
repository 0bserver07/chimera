---
name: bash-pipelines-with-care
description: Build bash pipelines deliberately — they are powerful, fragile, and easy to misread.
triggers: ["pipe", "pipeline", "shell", "xargs", "awk", "sed"]
---
## Bash pipelines, used carefully

Pipelines (`a | b | c`) are a small model's favourite hammer and a
common source of silent breakage. They reward fluency and punish
sloppiness. Use them, but use them with a few guardrails.

**Pre-flight checks before piping:**

- Run each stage in isolation first. `find . -name '*.py'` on its own
  before `find . -name '*.py' | xargs wc -l`. If the input stage is
  wrong, no amount of polishing the rest helps.
- Know what each stage outputs. `ls` and `find` differ on dotfiles
  and trailing newlines. `grep -l` outputs filenames; `grep` outputs
  lines. The pipe is silent about the type mismatch.
- Quote variables that may contain spaces or globs. `"$file"`, not
  `$file`.

**Quirks that bite small models:**

- `set -o pipefail` is **not** the default. Without it, `false |
  true` exits 0. If you care whether the pipeline succeeded as a
  whole, prepend `set -euo pipefail` or check `${PIPESTATUS[@]}`.
- `xargs` splits on whitespace by default, which breaks on filenames
  with spaces. Use `-0` with `find -print0` (or `-d '\n'` on GNU
  xargs) to be safe.
- `head` closes the pipe early. The upstream stage will receive
  SIGPIPE and look "killed" in logs. That is correct behaviour, but
  it can mask real errors.
- `awk` and `sed` syntax differs between BSD (macOS) and GNU
  (Linux). The portable subset is small. If a pipeline runs locally
  but fails in CI, suspect this first.

**Useful, less-known stages:**

- `grep -E` (or `egrep`) for extended regex without `\(` escaping.
- `sort -u` for sort-and-dedup in one step.
- `cut -f` over `awk '{print $1}'` when you genuinely just want
  field 1 — easier to read.
- `column -t` for human-readable tabular output at the end of a
  pipeline.
- `tee /tmp/debug` mid-pipeline to capture the intermediate stream
  while still flowing it forward.

**Things to avoid:**

- Pipelines longer than four stages without inline comments. By
  stage five, you're writing a program. Save it to a script.
- `cat file | grep pattern` — `grep pattern file` is shorter and
  doesn't spawn an extra process. (Useless Use of Cat is a real
  smell.)
- `ls | xargs ...` on filenames with spaces. Use `find -print0` or
  `globs + a for-loop`.
- `... | bash` from untrusted input. Ever.

**When in doubt:**

- Add `| head -20` to large outputs while developing. Remove it
  after you've confirmed the shape.
- Reach for Python (`subprocess` or a small script) when the
  pipeline approaches awk-mode complexity. The readability tradeoff
  flips around stage four.

A pipeline you understand is a pipeline you can debug. A pipeline
you wrote by reflex is a pipeline you'll be paged about.
