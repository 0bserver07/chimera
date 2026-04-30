---
name: core-tools
description: Effective use of Read, Write, Edit, and Bash for small models.
triggers: ["read", "write", "edit", "bash", "tool"]
---
## Core tools for small-model coding

The four tools that earn their context budget on almost every task. Use
them with intent — small models that fire tools speculatively burn the
window before they reach the real work.

**Read.** Pull a file's current contents into your working set. Always
prefer this over guessing what a file says.
- Pass an absolute path. Relative paths break across `cwd` resets.
- Read in slices when the file is large. Specify an offset/line-limit
  rather than reading 2,000 lines you'll mostly throw away.
- Re-read after any external change (a `Bash` command that touched the
  file, a previous `Edit`). Stale content is the most common cause of
  bad patches.

**Write.** Create a new file from scratch.
- Only for files that do not yet exist. If a file exists, use `Edit`.
- Always pass an absolute path. Confirm the parent directory exists
  first; create it with `mkdir -p` if not.
- Write the whole file at once. Don't try to "append" by re-writing —
  that's an `Edit` job.

**Edit.** Modify an existing file in place by replacing exact text.
- `old_string` must be byte-exact, including indentation and trailing
  newlines. Copy from a recent `Read`.
- `old_string` must be unique. Widen it with surrounding context if
  not.
- `new_string` is the *full* replacement, not a diff. Whatever you
  removed in `old_string` and want to keep must reappear in `new_string`.
- Use `replace_all` only when you genuinely want every occurrence
  changed (renaming a variable across the file).

**Bash.** Run a shell command in the project's environment.
- Always use absolute paths in arguments — the working directory may
  reset between calls.
- Combine sequential dependent commands with `&&` so failures
  short-circuit; use `;` only when you really don't care about earlier
  failures.
- Quote paths that may contain spaces.
- For long-running processes (servers, watchers), use a background
  invocation, not a foreground call that blocks.
- Capture both stderr and stdout when diagnosing (`2>&1` or the
  equivalent flag) so you don't miss the actual error.

Default budget: most tasks finish inside 6–10 tool calls. If you are
past 12 calls and the work still feels open-ended, stop and condense
your plan before continuing.
