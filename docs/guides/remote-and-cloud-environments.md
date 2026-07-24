# Remote and cloud environments

Chimera agents do not care *where* their tools run. Every file read, file
write, glob, and shell command goes through one interface —
`chimera.env.base.Environment` — and swapping the implementation moves the
whole agent onto another machine without touching the agent, the loop, or the
tools.

This guide covers the backends that run somewhere other than your laptop:

| Backend      | `create_environment(...)` | Runs on                    | Extra                        | Credentials      |
|--------------|---------------------------|----------------------------|------------------------------|------------------|
| SSH          | `"ssh"`                   | Any host you can `ssh` to  | none (stdlib)                | your SSH config  |
| SSH (async)  | `"ssh-async"`             | Any host you can `ssh` to  | `chimera-run[ssh]`           | your SSH config  |
| Docker       | `"docker"`                | Local container            | `chimera-run[docker]`        | Docker daemon    |
| Modal        | `"modal"`                 | Modal cloud sandbox        | `chimera-run[modal-sandbox]` | `~/.modal.toml`  |
| E2B          | `"e2b"`                   | E2B microVM                | `chimera-run[e2b]`           | `E2B_API_KEY`    |
| Daytona      | `"daytona"`               | Daytona sandbox            | `chimera-run[daytona]`       | `DAYTONA_API_KEY`|
| Remote HTTP  | `"remote"`                | A Chimera workspace server | `chimera-run[remote]`        | bearer token     |
| Cloud HTTP   | `"cloud"`                 | A provisioning API         | `chimera-run[remote]`        | bearer token     |

`create_environment("local")` and `"git"` round out the set; both are stdlib
only and need no credentials.

## One factory, every backend

```python
from chimera.env.factory import available_providers, create_environment

print(available_providers())
# ['cloud', 'daytona', 'docker', 'e2b', 'git', 'local', 'modal',
#  'remote', 'ssh', 'ssh-async']

with create_environment("daytona", image="python:3.11-slim") as env:
    env.write_file("main.py", "print('hello from the cloud')")
    print(env.run_command("python main.py").stdout)
```

Every backend implements the same nine methods — `setup`, `cleanup`,
`read_file`, `write_file`, `list_files`, `run_command`, `run_tests`,
`checkpoint`, `restore` — plus an optional `clone()`. Anything that accepts an
`Environment` accepts all of them. `checkpoint` / `restore` are real on the SSH
backends (tar snapshots of the remote workdir) and on `local` / `git`; the
ephemeral cloud sandboxes and an attached Docker container raise
`NotImplementedError` instead of pretending.

Register your own with `register_environment("my-cloud", MyEnvironment)`.

### Glob semantics are identical everywhere

`list_files(pattern)` follows `pathlib.Path.glob` rules on **every** backend: a
single `*` stops at the path separator, `**` spans segments. Backends that
enumerate paths remotely filter through `chimera.env.base.glob_match` so a
pattern selects the same files no matter what is mounted — otherwise a
benchmark's file counts would drift with its sandbox.

```python
env.list_files("*.py")     # top-level only
env.list_files("**/*.py")  # recursive
```

## Cloud sandboxes fail loudly, never quietly

Every managed-sandbox backend refuses to construct when its SDK or its
credentials are missing:

```python
>>> create_environment("daytona")
ValueError: DaytonaEnvironment requires a Daytona API key. Pass api_key=... or
set $DAYTONA_API_KEY. Refusing to continue: a cloud sandbox backend must never
silently fall back to local execution, because results produced locally would
be indistinguishable from results produced in the cloud.
```

This is deliberate and load-bearing. Chimera exists to compare agents under
controlled variables; a sandbox that quietly degraded to your laptop would
silently invalidate every result it touched, and nothing downstream could tell.
The same rule holds at the CLI, which exits `2` rather than running.

## E2B

```bash
pip install 'chimera-run[e2b]'
export E2B_API_KEY=e2b_...      # https://e2b.dev/dashboard
```

```python
from chimera.env.factory import create_environment

with create_environment(
    "e2b",
    template="base",          # E2B template name
    working_dir="/home/user",
    timeout=300,              # sandbox lifetime; E2B reaps it after this
) as env:
    env.write_file("app.py", "print(21 * 2)")
    print(env.run_command("python app.py").stdout)   # -> 42
```

Notes:

- `sandbox_id="sbx-..."` reconnects to an existing sandbox instead of creating
  one; `keep_alive=True` leaves it running at `cleanup()` (it keeps billing).
- `checkpoint()` / `restore()` raise `NotImplementedError` — E2B sandboxes are
  ephemeral. Use `"docker"` or `"ssh"` when a benchmark needs snapshots.

## Daytona

```bash
pip install 'chimera-run[daytona]'
export DAYTONA_API_KEY=dtn_...   # https://app.daytona.io
# optional:
export DAYTONA_API_URL=https://app.daytona.io/api
export DAYTONA_TARGET=us
```

```python
from chimera.env.factory import create_environment

with create_environment(
    "daytona",
    image="python:3.11-slim",     # or snapshot="my-snapshot" (not both)
    working_dir="/home/daytona",
    env_vars={"PYTHONUNBUFFERED": "1"},
) as env:
    env.write_file("app.py", "print(21 * 2)")
    print(env.run_command("python app.py").stdout)   # -> 42
```

Notes:

- `image=` and `snapshot=` are mutually exclusive; supplying neither uses the
  account default.
- Daytona returns a command's output as one consolidated stream, so
  `CommandResult.stdout` carries everything and `stderr` is usually empty.
- `checkpoint()` / `restore()` raise `NotImplementedError`.
- `keep_alive=True` skips deletion at `cleanup()` for post-mortem debugging.

## Running a benchmark matrix in the cloud

`chimera bench-matrix --env` selects the per-task environment. Each task gets a
**fresh** sandbox, so no task can contaminate the next:

```bash
# Modal (auth via `modal setup`, which writes ~/.modal.toml)
chimera bench-matrix --agents react --benchmarks mbpp --env modal --limit 20

# E2B
export E2B_API_KEY=e2b_...
chimera bench-matrix --agents react --benchmarks mbpp \
    --env e2b --sandbox-image base --limit 20

# Daytona
export DAYTONA_API_KEY=dtn_...
chimera bench-matrix --agents react --benchmarks mbpp \
    --env daytona --sandbox-image python:3.11-slim --limit 20
```

`--sandbox-image` is the E2B template name or the Daytona image; omit it for
the service default. Without credentials the command prints which variable is
missing and exits `2`.

> **Cost.** Every task provisions a billable sandbox. Start with `--limit 5`
> and a budget (`--max-cost`, `--max-tool-calls`) before running a full column.

## Remote execution over SSH

SSH is the backend for machines you already own — a build box, a GPU host, a
staging server. Two implementations share one interface:

| | `"ssh"` (default) | `"ssh-async"` |
|---|---|---|
| Dependency | none — drives the system `ssh`/`scp` | `chimera-run[ssh]` (asyncssh) |
| Connection | one process per call (or `ControlMaster`) | one persistent connection |
| File I/O | `ssh cat` / `ssh tee` — text only | native SFTP — binary safe |
| Bastions | your `~/.ssh/config` | native `ProxyJump` chains |
| Passwords | `ssh-add` first | `password=` / `passphrase=` accepted |
| Concurrency | serial | `run_bash_many` / `upload_files`, bounded |

Both implement the full ABC including `checkpoint()` / `restore()`, which tar
the remote workdir into `$HOME/.chimera/ssh-checkpoints`.

```python
from chimera.env.factory import create_environment

with create_environment(
    "ssh-async",
    host="build.example.com",
    username="deploy",
    workdir="/srv/app",
    proxy_jump="bastion.example.com",       # or "j1,j2" for a chain
    client_keys=["/home/me/.ssh/id_ed25519"],
    retries=3,                              # exponential backoff on connect
    max_concurrency=5,
) as env:
    env.write_file("patch.py", "...")
    print(env.run_bash("pytest -q").stdout)
    results = env.run_bash_many(["ruff check .", "mypy .", "pytest -q"])
```

Relative paths resolve against `workdir`; absolute paths are used as-is. SFTP
has no notion of a remote `cd`, so the async backend materialises absolute
paths itself and prefixes `cd <workdir> &&` onto every shell command.

### From the CLI

```bash
# Subprocess backend — no extra deps.
chimera mink --remote ssh://deploy@build.example.com:/srv/app -p "ls -la"

# asyncssh backend — SFTP, persistent connection, ProxyJump.
pip install 'chimera-run[ssh]'
CHIMERA_SSH_BACKEND=async chimera mink \
    --remote ssh://deploy@build.example.com:/srv/app -p "ls -la"
```

`docs/mink/remote.md` documents the URL grammar, the authentication matrix,
and the containerised live-test fixture in full.

### Which one should I use?

Reach for `"ssh-async"` when the workload is chatty (many small reads and
writes), binary-safe transfer matters, you cross a bastion, or the session is
long enough that per-call handshakes dominate. Stay on `"ssh"` for one-off
invocations and anywhere installing `asyncssh` is not worth it.

## Testing against these backends

Unit tests never touch the network. Each backend is driven through a fake SDK
or transport injected at the module boundary, so the whole suite runs in CI
where no optional extra is installed:

| Backend | Fake injected at | Tests |
|---|---|---|
| E2B | `chimera.env.e2b.Sandbox` | `tests/env/test_e2b.py` |
| Daytona | `chimera.env.daytona._sdk` | `tests/env/test_daytona.py` |
| SSH (async) | `chimera.env.ssh.asyncssh` | `tests/env/test_ssh_contract.py` |
| Glob parity | — (real `LocalEnvironment`) | `tests/env/test_glob_match.py` |

Live checks are opt-in and cost money or need infrastructure:

```bash
# SSH against a throwaway containerised sshd (needs Docker).
uv run pytest -m live_ssh

# E2B — one real sandbox.
export E2B_API_KEY=e2b_...
uv run python -c "
from chimera.env.factory import create_environment
with create_environment('e2b') as env:
    env.write_file('t.py', \"print('e2b ok')\")
    print(env.run_command('python t.py').stdout)
"

# Daytona — one real sandbox.
export DAYTONA_API_KEY=dtn_...
uv run python -c "
from chimera.env.factory import create_environment
with create_environment('daytona', image='python:3.11-slim') as env:
    env.write_file('t.py', \"print('daytona ok')\")
    print(env.run_command('python t.py').stdout)
"
```

Each smoke provisions one billable sandbox and deletes it on exit.

## Related

- `docs/mink/remote.md` — the `--remote ssh://…` CLI surface in depth.
- `docs/specs/agent-benchmark-matrix.md` — the matrix runner these backends
  feed.
- `chimera/env/factory.py` — the provider registry.
