# Checkpoints

The checkpoints module provides named, metadata-rich checkpoints on top of Chimera's environment snapshot/restore primitives. Use it to save and restore the state of a working environment at meaningful points during an agent session -- before risky operations, after successful milestones, or as an undo mechanism.

## Quick Start

```python
from chimera.checkpoints import CheckpointManager
from chimera.env.local import LocalEnvironment

env = LocalEnvironment(working_dir="/tmp/project")
manager = CheckpointManager(env)

# Save a checkpoint
cp = manager.create(name="before-refactor", description="Clean state before refactoring")
print(cp.time_str)  # "2026-03-06 14:30:00"

# ... make changes ...

# Undo to the most recent checkpoint
manager.undo()
```

## Key Classes

| Class | Module | Description |
|-------|--------|-------------|
| `CheckpointInfo` | `chimera.checkpoints` | Dataclass holding checkpoint metadata: `id`, `name`, `timestamp`, `description`. The `time_str` property returns a human-readable timestamp. |
| `CheckpointManager` | `chimera.checkpoints` | Wraps `Environment.checkpoint()` / `Environment.restore()` with named checkpoints, lookup by name or ID, undo, and listing. |

## Usage

### Creating and listing checkpoints

```python
from chimera.checkpoints import CheckpointManager

manager = CheckpointManager(env)

cp1 = manager.create(name="initial", description="Starting state")
cp2 = manager.create(name="after-tests", description="All tests passing")

for cp in manager.list_checkpoints():
    print(f"{cp.name} ({cp.time_str}): {cp.description}")
```

### Restoring by name or ID

```python
# Restore by name (finds most recent match)
restored = manager.restore_by_name("initial")
print(f"Restored to: {restored.name}")

# Restore by raw checkpoint ID
restored = manager.restore_by_id(cp1.id)
```

### Undo (restore most recent)

```python
result = manager.undo()
if result:
    print(f"Undone to: {result.name}")
else:
    print("No checkpoints to undo to")
```

### Auto-generated names

If you omit the `name` parameter, names are generated sequentially:

```python
cp = manager.create()  # name = "checkpoint-1"
cp = manager.create()  # name = "checkpoint-2"
```

### Lookup without restoring

```python
info = manager.get("before-refactor")
if info:
    print(f"Checkpoint exists: {info.id} at {info.time_str}")
```

## Integration

- **LoopConfig**: The `checkpoint_manager` field on `LoopConfig` injects a `CheckpointManager` into the agent loop. When set, the loop can create checkpoints automatically at configurable intervals.
- **REPL `/checkpoint` command**: The interactive CLI exposes checkpoint management via `/checkpoint create <name>`, `/checkpoint list`, `/checkpoint restore <name>`, and `/checkpoint undo`.
- **Environment**: `CheckpointManager` delegates to `Environment.checkpoint()` (which returns a raw ID string) and `Environment.restore(id)`. The `GitEnvironment` uses git commits; `DockerEnvironment` uses container snapshots; `LocalEnvironment` uses filesystem copies.
- **auto_checkpoint**: Set `manager.auto_checkpoint = True` to enable automatic checkpoint creation (the loop or calling code must check this flag and call `create()` accordingly).

## Import Reference

```python
from chimera.checkpoints import CheckpointInfo, CheckpointManager
```
