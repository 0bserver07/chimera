---
name: edit-before-write
description: Always reach for Edit before Write when changing an existing file.
triggers: ["edit file", "modify file", "change file", "update file", "patch"]
---
## Edit-before-write protocol

For any file that already exists on disk, the default tool is `Edit`, not
`Write`. `Write` replaces the entire file, which is a much larger blast
radius than is usually warranted and a common cause of small-model
regressions (truncated files, stripped imports, lost comments).

The rule:

1. If the file exists, the change is `Edit` unless you can name a reason
   why the whole file needs to be rewritten. "I want to be sure" is not a
   reason; "the file is 800 lines of generated code" is.
2. If the file does not exist, `Write` is correct. Confirm with `ls` or
   `Read` first if you are not certain.
3. If `Edit` fails with "string not found", do **not** fall back to
   `Write`. Re-`Read` the file to get the exact current bytes (whitespace
   often differs from what you remember), then retry `Edit` with the
   corrected `old_string`.
4. If `Edit` fails with "found multiple times", widen the `old_string`
   with two or three lines of surrounding context until it is unique.

Why this matters specifically for small models: they are biased toward
"start over" recovery. They will see a single `Edit` failure and
regenerate the entire file from imagination, which loses content the user
never asked you to change. The protocol locks the recovery into the
`Read → Edit` cycle that preserves what is on disk.

Pre-flight checklist before every `Edit`:

- I have the current file contents in my context (either freshly read or
  obviously unchanged since I read it).
- My `old_string` is copy-pasted from the read, not retyped from memory.
- My `old_string` is unique within the file.
- My `new_string` is the *complete* replacement, including any
  surrounding context I included in `old_string`.

If any of those is uncertain, `Read` the file again before calling `Edit`.
A re-read costs a few hundred tokens; a wrong `Edit` costs the user's
trust.
