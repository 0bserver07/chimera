---
title: Mink Remote Execution
description: "Route chimera mink file and bash tool calls through an SSH connection with --remote ssh://user@host."
---

# Remote execution over SSH (`--remote`)

`chimera mink` can route every file and bash tool call through an SSH
connection so the agent can read, write, and run commands on a remote
host without leaving your local terminal. This document covers the
scaffold landed in issue #127; production hardening (key passphrase
prompts, sudo escalation, ProxyJump UX) is tracked as follow-up work.

## Quick start

```bash
chimera mink --remote ssh://deploy@build.example.com:/srv/app -p "ls -la"
```

The URL form mirrors `git`/`scp`:

| Component | Required | Example                | Default |
|-----------|----------|------------------------|---------|
| scheme    | optional | `ssh://`               | implied |
| user      | optional | `deploy@`              | local user |
| host      | yes      | `build.example.com`    | —       |
| port      | optional | `:2222`                | `22`    |
| path      | optional | `/srv/app`             | remote home |

Bare `user@host` (no scheme, no path) is also accepted as a convenience.

## Authentication

The scaffold uses your existing OpenSSH client, so any setup that works
for an interactive `ssh user@host` shell will work here:

- **SSH agent** (`ssh-add ~/.ssh/id_ed25519`) — recommended.
- **Identity files** in `~/.ssh/config` — picked up automatically.
- **Programmatic identity** — pass `identity_file` when constructing
  `SSHEnvironment` directly from Python (the CLI relies on agent /
  config to keep the flag surface small).

Password and passphrase prompts are **not** supported in the scaffold.
If your key is passphrase-protected, unlock it with `ssh-add` before
launching `chimera mink`.

## Environment variables

| Variable                 | Effect                                    |
|--------------------------|-------------------------------------------|
| `CHIMERA_SSH_BACKEND`    | Selects the SSH backend. Set to `async` to opt in to the asyncssh-backed `AsyncSSHEnvironment` (native SFTP, persistent connection, ProxyJump). Any other value — including unset — keeps the subprocess `SSHEnvironment` default. The async path requires the `ssh` extra (`pip install 'chimera-run[ssh]'`); when `asyncssh` is missing, mink silently falls back to the subprocess backend so you never get a hard crash from a stray env var. |
| `CHIMERA_SSH_TEST_HOST`  | Enables the live integration tests in `tests/env/test_ssh_environment.py`. Set to a reachable `user@host`. |
| `SSH_AUTH_SOCK`          | Standard agent socket; `ssh` uses it.     |

### Picking a backend

```bash
# Default — subprocess / OpenSSH, zero extra deps.
chimera mink --remote ssh://deploy@host:/srv/app -p "ls"

# Opt in to asyncssh (native SFTP, persistent conn, ProxyJump chains).
pip install 'chimera-run[ssh]'
CHIMERA_SSH_BACKEND=async chimera mink \
    --remote ssh://deploy@host:/srv/app -p "ls"
```

The async backend is preferable for chatty workflows (many small file
reads/writes, multi-hop bastion topologies, or long-running sessions
where the per-call OpenSSH handshake becomes the bottleneck). The
subprocess backend remains the right default for one-off invocations
and environments where installing `asyncssh` isn't worth it.

## What gets routed

Once `--remote` is set, `chimera mink` swaps the default
`LocalEnvironment` for `SSHEnvironment` so every tool that goes through
the environment surface (bash, read, write, list_files, run_tests)
executes remotely. Tools that talk to the host filesystem directly
(e.g. anything reading `~/.chimera/sessions/`) are unaffected — those
remain local.

## Limitations (deferred to follow-up)

- **No SFTP** in the subprocess backend. File I/O uses `ssh cat` /
  `ssh tee`, which is fine for text but not binary-safe. Switch to
  `CHIMERA_SSH_BACKEND=async` for native SFTP.
- **No persistent connection** in the subprocess backend by default.
  Every call spawns a fresh `ssh`. For high-volume workflows, either
  configure `ControlMaster auto` in your SSH config or switch to
  `CHIMERA_SSH_BACKEND=async` (one persistent connection for the
  session).
- **No password / passphrase prompts** in the subprocess backend.
  Unlock keys with `ssh-add`. The async backend accepts `password=` /
  `passphrase=` when constructed programmatically.
- **No sudo escalation.** Run as a user with the right permissions.
- **`run_tests()` returns raw output.** Pytest output parsing is local-only.

## Programmatic use

```python
from chimera.env.ssh import SSHEnvironment

env = SSHEnvironment(
    host="deploy@build.example.com",
    workdir="/srv/app",
    port=2222,
    identity_file="/home/me/.ssh/deploy_ed25519",
    ssh_options={"StrictHostKeyChecking": "yes"},
)
env.setup()  # probes reachability via `ssh <host> true`
try:
    result = env.run_bash("git status --porcelain")
    print(result.stdout)
finally:
    env.cleanup()
```

## Live testing

The unit tests in `tests/env/test_ssh.py` and
`tests/env/test_ssh_environment.py` run against mocked
`subprocess.run`, which catches argv-shape regressions but can't
exercise the wire format end-to-end. For that, an opt-in suite in
`tests/env/test_ssh_live.py` boots a real `linuxserver/openssh-server`
container (via the `docker_sshd` fixture in `tests/env/conftest.py`)
and runs `SSHEnvironment` against it.

### Opt in

```bash
uv run pytest -m live_ssh                  # only the live SSH tests
uv run pytest tests/env/ -m "not live_ssh" # explicit opt-out
```

When Docker isn't installed or its daemon is unreachable, the live
tests are **skipped with a helpful message** — they never fail
spuriously on a host without Docker.

### Requirements

- A reachable Docker daemon (`docker info` exits 0).
- The `linuxserver/openssh-server:latest` image. The fixture pulls it
  on first use; offline runs need it cached locally.
- `ssh-keygen` on `PATH` — the fixture generates a fresh ed25519
  keypair per session and discards it at teardown.

### What the fixture does

1. Probes Docker via `docker info`. Daemon down → skip with message.
2. Generates an ephemeral ed25519 keypair into a session-scoped tmp dir.
3. Runs:

   ```
   docker run -d --rm \
     -p 0:2222 \
     -e USER_NAME=test \
     -e PUBLIC_KEY=<generated> \
     -e PASSWORD_ACCESS=false \
     -e SUDO_ACCESS=false \
     linuxserver/openssh-server:latest
   ```

   Port `0` lets the kernel pick a free host port; the fixture reads
   the mapping back via `docker port <id> 2222/tcp`.
4. Waits up to 30 s for the SSH banner before yielding.
5. Tears the container down via `docker rm -f` in the finalizer
   (with `--rm` as belt-and-suspenders cleanup if pytest is killed
   mid-run).

The fixture yields an `SshdEndpoint(host, port, username, key_path,
container_id)` namedtuple that tests pass straight to
`SSHEnvironment(...)`.

### Adding new live tests

Tag the test with `@pytest.mark.live_ssh` (or apply
`pytestmark = pytest.mark.live_ssh` at module scope), then accept the
`docker_sshd` fixture:

```python
import pytest
from chimera.env.ssh import SSHEnvironment

@pytest.mark.live_ssh
def test_remote_whoami(docker_sshd):
    env = SSHEnvironment(
        host=f"{docker_sshd.username}@{docker_sshd.host}",
        port=docker_sshd.port,
        identity_file=docker_sshd.key_path,
        ssh_options={
            "StrictHostKeyChecking": "no",
            "UserKnownHostsFile": "/dev/null",
        },
    )
    env.setup()
    try:
        assert env.run_bash("whoami").stdout.strip() == "test"
    finally:
        env.cleanup()
```

## Related

- Issue [#127](https://github.com/0bserver07/chimera/issues/127) — full
  spec and roadmap (asyncssh-backed `Backend` protocol, contextvars
  swap, SFTP).
- `chimera/env/remote.py` — the older HTTP-workspace transport, kept
  for environments that already run a Chimera workspace server.
