"""Tests for chimera.core.task_manager — BackgroundTask and TaskManager."""
from __future__ import annotations

import tempfile
from pathlib import Path


from chimera.core.task_manager import BackgroundTask, TaskManager


# ---------------------------------------------------------------------------
# Test 1: BackgroundTask creation
# ---------------------------------------------------------------------------


def test_background_task_creation():
    task = BackgroundTask(
        task_id="task-1",
        agent_id="agent-1",
        description="Run tests",
        status="running",
        output_path=Path("/tmp/task-1-output.txt"),
    )

    assert task.task_id == "task-1"
    assert task.agent_id == "agent-1"
    assert task.description == "Run tests"
    assert task.status == "running"
    assert task.output_path == Path("/tmp/task-1-output.txt")
    assert task.started_at is not None
    assert task.completed_at is None


# ---------------------------------------------------------------------------
# Test 2: TaskManager.register and get
# ---------------------------------------------------------------------------


def test_task_manager_register_and_get():
    manager = TaskManager()
    task = manager.register(
        agent_id="agent-1",
        description="Build project",
    )

    assert task.task_id is not None
    assert task.agent_id == "agent-1"
    assert task.description == "Build project"
    assert task.status == "running"

    retrieved = manager.get(task.task_id)
    assert retrieved is task


def test_task_manager_get_nonexistent():
    manager = TaskManager()
    assert manager.get("nonexistent") is None


# ---------------------------------------------------------------------------
# Test 3: TaskManager.list_tasks
# ---------------------------------------------------------------------------


def test_task_manager_list_tasks():
    manager = TaskManager()
    t1 = manager.register(agent_id="a1", description="Task 1")
    t2 = manager.register(agent_id="a2", description="Task 2")
    t3 = manager.register(agent_id="a3", description="Task 3")

    all_tasks = manager.list_tasks()
    assert len(all_tasks) == 3
    assert {t.task_id for t in all_tasks} == {t1.task_id, t2.task_id, t3.task_id}


def test_task_manager_list_tasks_empty():
    manager = TaskManager()
    assert manager.list_tasks() == []


# ---------------------------------------------------------------------------
# Test 4: TaskManager.stop
# ---------------------------------------------------------------------------


def test_task_manager_stop():
    manager = TaskManager()
    task = manager.register(agent_id="a1", description="Long task")

    assert task.status == "running"

    manager.stop(task.task_id)
    updated = manager.get(task.task_id)
    assert updated is not None
    assert updated.status == "stopped"
    assert updated.completed_at is not None


def test_task_manager_stop_nonexistent():
    manager = TaskManager()
    # Should not raise, just silently do nothing
    manager.stop("nonexistent")


# ---------------------------------------------------------------------------
# Test 5: TaskManager.read_output
# ---------------------------------------------------------------------------


def test_task_manager_read_output():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("Task output line 1\nTask output line 2\n")
        f.flush()
        output_path = Path(f.name)

    try:
        manager = TaskManager()
        task = manager.register(
            agent_id="a1",
            description="Readable task",
            output_path=output_path,
        )

        content = manager.read_output(task.task_id)
        assert content is not None
        assert "Task output line 1" in content
        assert "Task output line 2" in content
    finally:
        output_path.unlink()


def test_task_manager_read_output_no_file():
    manager = TaskManager()
    task = manager.register(
        agent_id="a1",
        description="No output file",
        output_path=Path("/nonexistent/output.txt"),
    )

    content = manager.read_output(task.task_id)
    assert content is None


def test_task_manager_read_output_nonexistent_task():
    manager = TaskManager()
    content = manager.read_output("nonexistent")
    assert content is None


# ---------------------------------------------------------------------------
# Test 6: BackgroundTask default values
# ---------------------------------------------------------------------------


def test_background_task_defaults():
    task = BackgroundTask(
        task_id="t1",
        agent_id="a1",
        description="test",
    )

    assert task.status == "running"
    assert task.output_path is None
    assert task.started_at is not None
    assert task.completed_at is None


# ---------------------------------------------------------------------------
# Test 7: Multiple register calls produce unique task_ids
# ---------------------------------------------------------------------------


def test_task_manager_unique_ids():
    manager = TaskManager()
    tasks = [manager.register(agent_id="a", description="t") for _ in range(10)]
    task_ids = [t.task_id for t in tasks]
    assert len(set(task_ids)) == 10


# ---------------------------------------------------------------------------
# Test 8: Completing a task
# ---------------------------------------------------------------------------


def test_task_manager_complete():
    manager = TaskManager()
    task = manager.register(agent_id="a1", description="Completable")

    manager.complete(task.task_id)
    updated = manager.get(task.task_id)
    assert updated is not None
    assert updated.status == "completed"
    assert updated.completed_at is not None
