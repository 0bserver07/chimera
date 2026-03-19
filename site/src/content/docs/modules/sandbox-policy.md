---
title: "Sandbox Policy"
description: "Sandbox Policy"
---

`chimera.security.sandbox` defines declarative sandbox policies for agent
execution. A `SandboxPolicy` describes filesystem, network, and command
restrictions that environments can enforce. Inspired by Codex's Seatbelt
SBPL policies. The policy is declarative -- environments enforce it.

## Key Classes

| Class | Description |
|-------|-------------|
| `SandboxPolicy` | Declarative policy with path, network, and command rules |
| `PathRule` | Filesystem access rule: path + access level + recursive flag |
| `NetworkRule` | Network access rule: host + port + allow/deny |
| `AccessLevel` | Enum: `DENY`, `READ`, `WRITE`, `EXECUTE` |

## Quick Start

```python
from chimera.security.sandbox import SandboxPolicy, PathRule, AccessLevel

policy = SandboxPolicy(
    name="project",
    path_rules=[
        PathRule(path="/my/project", access=AccessLevel.WRITE, recursive=True),
        PathRule(path="/", access=AccessLevel.READ, recursive=True),
    ],
    denied_commands=["rm", "sudo", "chmod"],
    timeout_seconds=300,
)

policy.check_path("/my/project/src/main.py", AccessLevel.WRITE)  # True
policy.check_path("/etc/passwd", AccessLevel.WRITE)               # False
policy.check_command("sudo apt install")                           # False
```

## Presets

Three built-in presets cover common scenarios:

```python
# No restrictions -- for trusted environments
policy = SandboxPolicy.permissive()

# Read/write within workspace, read-only elsewhere
policy = SandboxPolicy.workspace_only("/my/project")

# Workspace write, no network, limited commands
policy = SandboxPolicy.strict("/my/project")
```

## Network Rules

```python
from chimera.security.sandbox import NetworkRule

policy = SandboxPolicy(
    network_rules=[
        NetworkRule(host="api.example.com", allow=True),
        NetworkRule(host="*", allow=False),  # deny everything else
    ],
)

policy.check_network("api.example.com")   # True
policy.check_network("evil.example.com")  # False
```

## Import Reference

```python
from chimera.security.sandbox import SandboxPolicy, PathRule, NetworkRule, AccessLevel
```

## Related

- [Security](security.md) -- risk classification and security analysis
- [Permissions](permissions.md) -- tool-level permission policies
