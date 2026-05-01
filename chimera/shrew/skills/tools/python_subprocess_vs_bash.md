---
name: python-subprocess-vs-bash
description: When to drop into Python's subprocess module instead of writing bash.
triggers: ["subprocess", "shell out", "python script", "bash script", "shell pipeline"]
---
## Python subprocess vs. bash

For one-off filesystem and process work, `bash` is fine. For anything
that needs structured data, retries, error inspection, or
cross-platform behaviour, drop into Python. The decision rule is
short: if the task has more than two `if` statements in it, write
Python.

**Bash wins when:**

- The task is a single command with arguments. `pytest -q tests/`,
  `ruff check chimera/`, `git status`. Wrapping these in Python adds
  noise.
- The task is a short pipeline of standard utilities. `find . -name
  '*.log' -delete` is one line and self-evident.
- You need the full POSIX environment to be in scope (PATH, aliases,
  shell builtins).

**Python wins when:**

- You need to parse a command's output. Bash has no JSON parser, no
  proper string types, and no real error handling. Python has all
  three.
- You need cross-platform paths, especially Windows compatibility.
  `pathlib.Path` works everywhere; bash mostly doesn't.
- You need retries with backoff, conditional logic on exit codes,
  or structured logging.
- You need to interpolate user-controlled data into commands. Use
  `subprocess.run([...])` with a list, not a shell string.

**`subprocess.run` checklist (Python):**

```python
import subprocess

result = subprocess.run(
    ["pytest", "-q", "tests/"],
    cwd="/abs/path/to/repo",
    capture_output=True,
    text=True,
    check=False,
    timeout=120,
)
if result.returncode != 0:
    print(result.stderr)
```

- **Pass a list, not a string.** `["cmd", "arg"]`, not `"cmd arg"`
  with `shell=True`. The list form is shell-injection-safe; the
  string form is not.
- **`check=False` + manual return-code inspection** when you want to
  recover. **`check=True`** when a non-zero exit should raise.
- **`capture_output=True, text=True`** to get strings back, not
  bytes.
- **Set `timeout`** for any command that could hang. Subprocesses
  that wait forever are how interactive sessions deadlock.
- **Set `cwd` explicitly** when you care which directory the command
  runs in. Don't rely on the parent process's cwd.

**`shell=True` warning:**

- Only use `shell=True` when you actually need shell features
  (globbing, redirection, pipes you can't restructure). When you
  do, *never* interpolate untrusted input into the command string.
  Use `shlex.quote()` if you have to.

**Common conversions:**

- `bash -c 'find . -name "*.py" | xargs wc -l'` →
  `subprocess.run(["wc", "-l"] + glob.glob("**/*.py", recursive=True))`,
  or just use `pathlib.Path.rglob` and a counter.
- `bash -c 'grep -l pattern *.py'` → loop with `re` and `pathlib`.
- `bash -c 'curl url | jq .field'` → `httpx.get(url).json()["field"]`.

**Anti-patterns:**

- Inventing a 30-line bash script with arrays, traps, and
  associative maps. That is Python wearing the wrong costume.
- Calling Python from bash to call bash from Python. Pick one
  language per script.
- `os.system(...)`. It's `subprocess.run`'s worse-tested ancestor;
  use the modern API.

The bar for staying in bash is "this would be one line in Python
too". Past that, switch.
