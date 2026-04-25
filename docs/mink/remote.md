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
| `CHIMERA_SSH_TEST_HOST`  | Enables the live integration tests in `tests/env/test_ssh_environment.py`. Set to a reachable `user@host`. |
| `SSH_AUTH_SOCK`          | Standard agent socket; `ssh` uses it.     |

No new env vars are introduced by this scaffold beyond the test toggle.

## What gets routed

Once `--remote` is set, `chimera mink` swaps the default
`LocalEnvironment` for `SSHEnvironment` so every tool that goes through
the environment surface (bash, read, write, list_files, run_tests)
executes remotely. Tools that talk to the host filesystem directly
(e.g. anything reading `~/.chimera/sessions/`) are unaffected — those
remain local.

## Limitations (deferred to follow-up)

- **No SFTP.** File I/O uses `ssh cat` / `ssh tee`, which is fine for
  text but not binary-safe.
- **No persistent connection.** Every call spawns a fresh `ssh`. For
  high-volume workflows, configure `ControlMaster auto` in your SSH
  config to amortize the connection cost.
- **No checkpoint/restore.** Use git on the remote host instead.
- **No password / passphrase prompts.** Unlock keys with `ssh-add`.
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

## Related

- Issue [#127](https://github.com/0bserver07/chimera/issues/127) — full
  spec and roadmap (asyncssh-backed `Backend` protocol, contextvars
  swap, SFTP).
- `chimera/env/remote.py` — the older HTTP-workspace transport, kept
  for environments that already run a Chimera workspace server.
