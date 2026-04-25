"""Tests for chimera.tools.notebook_edit.NotebookEditTool."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

nbformat = pytest.importorskip("nbformat")

from chimera.tools.notebook_edit import NotebookEditTool

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.ipynb"


@pytest.fixture()
def nb_path(tmp_path: Path) -> Path:
    """Copy the fixture into a temp dir so tests never mutate the original."""
    dst = tmp_path / "nb.ipynb"
    shutil.copy(FIXTURE, dst)
    return dst


def test_insert_cell(nb_path: Path) -> None:
    tool = NotebookEditTool()
    result = tool.execute(
        {"notebook_path": str(nb_path), "action": "insert", "cell_index": 1,
         "content": "y = 99", "cell_type": "code"},
        env=None,
    )
    assert result.success, result.error
    nb = nbformat.read(str(nb_path), as_version=4)
    assert len(nb.cells) == 4
    assert nb.cells[1].cell_type == "code"
    assert nb.cells[1].source == "y = 99"


def test_replace_cell(nb_path: Path) -> None:
    tool = NotebookEditTool()
    result = tool.execute(
        {"notebook_path": str(nb_path), "action": "replace", "cell_index": 0,
         "content": "print('hello')"},
        env=None,
    )
    assert result.success
    nb = nbformat.read(str(nb_path), as_version=4)
    assert nb.cells[0].source == "print('hello')"


def test_delete_cell(nb_path: Path) -> None:
    tool = NotebookEditTool()
    pre = nbformat.read(str(nb_path), as_version=4)
    pre_count = len(pre.cells)
    result = tool.execute(
        {"notebook_path": str(nb_path), "action": "delete", "cell_index": 1},
        env=None,
    )
    assert result.success
    nb = nbformat.read(str(nb_path), as_version=4)
    assert len(nb.cells) == pre_count - 1


def test_out_of_range(nb_path: Path) -> None:
    tool = NotebookEditTool()
    result = tool.execute(
        {"notebook_path": str(nb_path), "action": "delete", "cell_index": 99},
        env=None,
    )
    assert not result.success
    assert "out of range" in (result.error or "")


def test_insert_by_cell_id(nb_path: Path) -> None:
    nb = nbformat.read(str(nb_path), as_version=4)
    target_id = nb.cells[2].get("id")
    tool = NotebookEditTool()
    result = tool.execute(
        {"notebook_path": str(nb_path), "action": "replace",
         "cell_id": target_id, "content": "# replaced", "cell_type": "markdown"},
        env=None,
    )
    assert result.success, result.error
    nb2 = nbformat.read(str(nb_path), as_version=4)
    assert nb2.cells[2].source == "# replaced"
    assert nb2.cells[2].cell_type == "markdown"
