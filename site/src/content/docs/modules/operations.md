---
title: "Operations"
description: "Operations"
---

`chimera.core.operations` defines four `Protocol` interfaces for the
low-level I/O operations used by built-in tools.  Swapping in a custom
implementation lets tools work against Docker containers, remote servers,
or in-memory fixtures.

## Protocols

| Protocol | Methods | Description |
|----------|---------|-------------|
| `ReadOps` | `read_file(path)`, `file_exists(path)` | File reading backend |
| `WriteOps` | `write_file(path, content)` | File writing backend |
| `BashOps` | `run_command(command, timeout, cwd)` | Command execution backend |
| `SearchOps` | `search_files(pattern, path)`, `list_files(pattern)` | File search backend |

All four are `@runtime_checkable`, so `isinstance()` checks work without
subclassing.

## Local implementations

| Class | Protocol | Notes |
|-------|----------|-------|
| `LocalReadOps` | `ReadOps` | `open()` relative to `cwd` |
| `LocalWriteOps` | `WriteOps` | `mkdir -p` before writing |
| `LocalBashOps` | `BashOps` | `subprocess.run(shell=True)`, returns `CommandResult` |
| `LocalSearchOps` | `SearchOps` | `Path.rglob` with regex matching |

Each constructor accepts an optional `cwd: str = "."` argument.

## Example — custom ops for Docker

```python
from chimera.core.operations import ReadOps, BashOps
from chimera.types import CommandResult

class DockerReadOps:
    def __init__(self, container: str) -> None:
        self.container = container

    def read_file(self, path: str) -> str:
        import subprocess
        return subprocess.check_output(
            ["docker", "exec", self.container, "cat", path], text=True
        )

    def file_exists(self, path: str) -> bool:
        import subprocess
        r = subprocess.run(
            ["docker", "exec", self.container, "test", "-f", path]
        )
        return r.returncode == 0

# Pass to tools that accept a read_ops parameter
from chimera.tools import ReadTool
tool = ReadTool(read_ops=DockerReadOps("my-container"))
```

## CommandResult

`BashOps.run_command` returns a `CommandResult` (from `chimera.types`):

| Field | Type | Description |
|-------|------|-------------|
| `stdout` | `str` | Standard output |
| `stderr` | `str` | Standard error |
| `exit_code` | `int` | Process exit code (`-1` on timeout) |

<!-- W17-3 SOURCE FOOTER -->

## Source

`chimera/core/operations.py` -- verified against current source at wave-17.
