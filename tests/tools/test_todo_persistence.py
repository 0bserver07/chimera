"""Tests for chimera.tools.todo persistence."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.tools.todo import TodoTool, _project_todo_path, _user_todo_path


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point HOME at a tmp dir so the user-scope mirror doesn't leak."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def test_writes_then_restores(tmp_path: Path, isolated_home: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    tool = TodoTool(cwd=str(project), persist=True)
    tool.execute({"action": "add", "task": "first"})
    tool.execute({"action": "add", "task": "second"})
    tool.execute({"action": "add", "task": "third"})
    assert len(tool.items) == 3

    proj_file = _project_todo_path(str(project))
    assert proj_file.exists()
    data = json.loads(proj_file.read_text())
    assert len(data["items"]) == 3

    # Simulate restart: brand-new instance must rehydrate from disk.
    restored = TodoTool(cwd=str(project), persist=True)
    assert len(restored.items) == 3
    assert [it.task for it in restored.items] == ["first", "second", "third"]


def test_complete_persists(tmp_path: Path, isolated_home: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    tool = TodoTool(cwd=str(project), persist=True)
    tool.execute({"action": "add", "task": "do thing"})
    tool.execute({"action": "complete", "task": "1"})

    restored = TodoTool(cwd=str(project), persist=True)
    assert restored.items[0].done is True


def test_load_at_session_start(tmp_path: Path, isolated_home: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    tool = TodoTool(cwd=str(project), persist=True)
    tool.execute({"action": "add", "task": "alpha"})
    restored = TodoTool.load_at_session_start("sess-123", str(project))
    assert [it.task for it in restored.items] == ["alpha"]


def test_user_scope_mirror(tmp_path: Path, isolated_home: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    tool = TodoTool(cwd=str(project), persist=True)
    tool.execute({"action": "add", "task": "mirrored"})
    assert _user_todo_path(str(project)).exists()


def test_persist_disabled(tmp_path: Path, isolated_home: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    tool = TodoTool(cwd=str(project), persist=False)
    tool.execute({"action": "add", "task": "ephemeral"})
    assert not _project_todo_path(str(project)).exists()
