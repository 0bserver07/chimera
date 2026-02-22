# Persistent Shell Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add persistent shell sessions to Chimera environments so shell state (cd, export, background processes) survives between commands.

**Architecture:** A `SessionMixin` class using tmux as the backend, composable onto any Environment subclass. Supports multiple named shells per environment. BashTool and other tools auto-detect and route through the session transparently.

**Motivation:** Chimera's `LocalEnvironment.run_command()` spawns a fresh subprocess per command, losing all state. This blocks Terminal Bench (needs `export` persistence), SWE-bench (needs `cd` persistence), and any workflow involving background servers.

---

## Components

### 1. SessionMixin (`chimera/env/session.py`)

A mixin class that adds persistent shell capabilities via tmux.

**Public API:**
- `start_session(shell: str = "/bin/bash") -> None` — Spawn a tmux session
- `end_session() -> None` — Kill the tmux session and all shells
- `create_shell(name: str) -> None` — Create a new named shell (tmux window)
- `list_shells() -> list[str]` — List active shell names
- `has_session: bool` — Whether a session is active
- `run_in_session(cmd: str, shell_name: str = "main", timeout: int = 120) -> CommandResult` — Execute a command in a named shell, capture output

**tmux session naming:** `chimera-{uuid[:8]}` to avoid collisions.

**Output capture strategy:**
1. Wrap command: `echo __CHIMERA_START__{uuid}; <cmd>; echo __CHIMERA_END__{uuid}_$?__`
2. Send via `tmux send-keys ... Enter`
3. Poll `tmux capture-pane -p -t <session>:<window>` until end sentinel appears
4. Extract output between sentinels
5. Parse exit code from end sentinel

**Poll strategy:** Start at 50ms intervals, back off to 500ms after 2s. Timeout raises and returns `CommandResult(exit_code=124)`.

### 2. Environment ABC Changes (`chimera/env/base.py`)

Add optional `shell_name` parameter to `run_command()`:

```python
def run_command(self, cmd: str, timeout: int = 120, shell_name: str = "main") -> CommandResult:
```

Default `"main"` preserves backward compatibility. Tools that don't care about named shells ignore it.

### 3. LocalEnvironment Integration (`chimera/env/local.py`)

- Inherit from `SessionMixin` (MRO: `LocalEnvironment -> SessionMixin -> Environment`)
- Add `session: bool = False` constructor parameter
- If `session=True`, call `start_session()` during `setup()`
- Override `run_command()`: if `self.has_session`, delegate to `run_in_session()`; otherwise, use existing `subprocess.run` path
- `cleanup()` calls `end_session()` if active

### 4. DockerEnvironment Integration (`chimera/env/docker.py`)

- Same pattern: inherit `SessionMixin`
- Session runs tmux *inside* the container (requires tmux installed in image)
- `run_in_session()` wraps commands with `docker exec` prefix

### 5. Tool Transparency

BashTool, TestTool, GitTool, and all other tools call `env.run_command()`. No changes needed — the mixin intercepts at the environment level.

## What Doesn't Change

- `Environment` ABC remains abstract (mixin doesn't satisfy it)
- `read_file()`, `write_file()`, `list_files()` — unaffected
- `checkpoint()` / `restore()` — unaffected (filesystem-level, not shell-level)
- All existing tests pass without modification

## Testing Strategy

- Mock tmux via `subprocess.run` patches for unit tests
- Integration tests that actually spawn tmux (marked with `@pytest.mark.integration`)
- Test: `cd /tmp` then `pwd` returns `/tmp` (state persistence)
- Test: `export FOO=bar` then `echo $FOO` returns `bar`
- Test: named shells are independent (`cd /tmp` in "main" doesn't affect "server")
- Test: timeout handling
- Test: session cleanup on `end_session()` / `cleanup()`
- Test: backward compat — `session=False` behaves identically to current code
