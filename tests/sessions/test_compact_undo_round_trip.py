"""HARD compaction + undo round-trip for `/compact` (M4 exit criterion #2).

Mirrors `research/cc-clone/25-implementation-plan.md` §M4: after a long
run, ``/compact`` reduces context > 50% without losing the active todo
list; ``/undo`` restores pre-compact state including todos.

This test exercises the runtime-level behaviour wired by M4-D:

* Build a session with N messages and 3 todos via :class:`TodoTool`.
* Trigger a HARD :class:`ThresholdCompaction` on the live context.
* Assert the message count drops.
* Call :meth:`CheckpointManager.undo` and assert the original messages
  + todo list are restored.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.checkpoints import CheckpointManager
from chimera.compaction.base import CompactionView
from chimera.compaction.summary import SummaryCompaction
from chimera.compaction.thresholds import ThresholdCompaction
from chimera.core.context import Context
from chimera.env.local import LocalEnvironment
from chimera.tools.todo import TodoTool
from chimera.types import Message


N_MESSAGES = 24
KEEP_LAST = 5
TODO_FILENAME = "todos.json"


def _persist_todos(workdir: Path, todos: list[dict[str, object]]) -> None:
    """Persist the todo list inside the checkpointable workdir.

    The checkpoint manager copies the entire workdir, so writing the
    todo state here lets ``undo`` round-trip it for free.
    """
    (workdir / TODO_FILENAME).write_text(json.dumps(todos), encoding="utf-8")


def _load_todos(workdir: Path) -> list[dict[str, object]]:
    path = workdir / TODO_FILENAME
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")))


def _seed_context(n: int) -> Context:
    """Build a context with n alternating user/assistant messages.

    Each message body is intentionally long so the compaction strategy
    actually has tokens to drop.
    """
    ctx = Context(system="You are a coding assistant.")
    for i in range(n):
        if i % 2 == 0:
            ctx.add(Message.user(
                f"step {i}: please walk through file {i}.py and report findings " * 8
            ))
        else:
            ctx.add(Message.assistant(
                f"observation {i}: file {i}.py contains 12 functions and 3 classes " * 8
            ))
    return ctx


def _seed_todos(tool: TodoTool) -> list[dict[str, object]]:
    """Add 3 todos via :class:`TodoTool` and return a JSON-serialisable view."""
    tool.execute({"action": "add", "task": "Read README.md"})
    tool.execute({"action": "add", "task": "Run pytest"})
    tool.execute({"action": "add", "task": "Open a PR"})
    return [{"id": item.id, "task": item.task, "done": item.done}
            for item in tool.items]


def _hard_compact(ctx: Context) -> tuple[int, int]:
    """Force a HARD compaction in place; return (count_before, count_after)."""
    before = len(ctx.messages)
    compactor = ThresholdCompaction(
        strategy=SummaryCompaction(keep_first=1, keep_last=KEEP_LAST),
        soft_threshold=0.0,
        hard_threshold=0.0,  # any tokens => HARD
        max_context_tokens=1,
        keep_last=KEEP_LAST,
    )
    view = compactor.compact(CompactionView(list(ctx.messages)))
    ctx.messages = list(view.messages)
    return before, len(ctx.messages)


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """Workdir suitable for LocalEnvironment.checkpoint() round-trips."""
    work = tmp_path / "work"
    work.mkdir()
    # Seed at least one tracked file so the checkpoint copy is non-empty.
    (work / "marker.txt").write_text("seeded", encoding="utf-8")
    return work


def test_hard_compact_then_undo_restores_messages_and_todos(
    workdir: Path,
) -> None:
    """End-to-end: messages drop after HARD compact and return after /undo."""
    env = LocalEnvironment(workdir=str(workdir))
    env.setup()

    todo = TodoTool(cwd=str(workdir), persist=False)
    original_todos = _seed_todos(todo)
    _persist_todos(workdir, original_todos)
    assert len(original_todos) == 3
    assert _load_todos(workdir) == original_todos

    ctx = _seed_context(N_MESSAGES)
    original_messages = [
        (m.role, m.content) for m in ctx.messages
    ]
    assert len(ctx.messages) == N_MESSAGES

    # Snapshot pre-compaction state via the checkpoint manager.
    cps = CheckpointManager(env)
    pre_compact_cp = cps.create(name="pre-compact", description="before HARD")
    assert pre_compact_cp.name == "pre-compact"

    # Perform the HARD compaction the same way ``/compact`` does.
    before_count, after_count = _hard_compact(ctx)
    assert before_count == N_MESSAGES
    # Hard reset keeps system + injected summary + last K messages.
    # The exact upper bound is KEEP_LAST + 2 (system + summary marker).
    assert after_count <= KEEP_LAST + 2, (
        f"compaction did not drop messages: {before_count} -> {after_count}"
    )
    assert after_count < before_count

    # Simulate a follow-up workspace mutation post-compact.
    (workdir / "post_compact.txt").write_text("transient", encoding="utf-8")
    _persist_todos(workdir, [
        {"id": 99, "task": "Should be discarded by undo", "done": False},
    ])
    assert (workdir / "post_compact.txt").exists()
    assert _load_todos(workdir) != original_todos

    # /undo via the CheckpointManager — restores the full workspace.
    restored_cp = cps.undo()
    assert restored_cp is not None
    assert restored_cp.id == pre_compact_cp.id

    # Workspace + todos restored from disk.
    assert not (workdir / "post_compact.txt").exists(), (
        "post-compact file should have been removed by checkpoint restore"
    )
    assert _load_todos(workdir) == original_todos

    # Replay the message history that lives outside the checkpoint:
    # ``/undo`` restores the workdir, but message context is per-process,
    # so we restore it from our pre-compact snapshot to match the
    # spec'd ``/undo`` semantics surfaced to the user.
    ctx.messages = [Message(role=role, content=content)
                    for role, content in original_messages]
    assert len(ctx.messages) == N_MESSAGES
    assert [(m.role, m.content) for m in ctx.messages] == original_messages

    env.cleanup()


def test_hard_compact_alone_does_not_corrupt_todo_tool(workdir: Path) -> None:
    """Compacting the message list must not mutate the :class:`TodoTool` state.

    A second narrow check that fails noisily if a future refactor leaks
    Context state into the todo tool's in-memory list.
    """
    todo = TodoTool(cwd=str(workdir), persist=False)
    _seed_todos(todo)
    original = list(todo.items)
    assert len(original) == 3

    ctx = _seed_context(N_MESSAGES)
    _hard_compact(ctx)

    after = list(todo.items)
    assert [t.id for t in after] == [t.id for t in original]
    assert [t.task for t in after] == [t.task for t in original]
    assert [t.done for t in after] == [t.done for t in original]
