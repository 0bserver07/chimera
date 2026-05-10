---
title: "chimera.env"
description: "Reference for chimera.env — Environment ABC and Local/Git/Docker/Remote/Cloud/PersistentShell impls."
---

`chimera.env` is the execution-environment layer: the place tools
actually run.

## Top-level exports

```python
from chimera.env import (
    Environment,
    LocalEnvironment,
    GitEnvironment,
    DockerEnvironment,
    RemoteEnvironment,
    CloudEnvironment,
    PersistentShell,
)
```

| Symbol | Module | Use |
|---|---|---|
| `Environment` | `chimera.env.base` | ABC. `run(command)`, `read(path)`, `write(path, content)`. |
| `LocalEnvironment` | `chimera.env.local` | Direct filesystem / subprocess. |
| `GitEnvironment` | `chimera.env.git` | Branch isolation: every run mutates a worktree branch you can roll back. |
| `DockerEnvironment` | `chimera.env.docker` | Container isolation. |
| `RemoteEnvironment` | `chimera.env.remote` | HTTP client to a remote env server. Optional `httpx` extra. |
| `CloudEnvironment` | `chimera.env.cloud` | Managed sandbox provisioning. |
| `PersistentShell` | `chimera.env.shell` | tmux-backed long-lived shell session. |

`Environment` is the second positional argument to every tool's
`execute(args, env)`. Tools that don't touch the filesystem can ignore
it and accept `None`.

## See also

- [Add a Custom Tool](/add-custom-tool/) for the tool / environment contract.
