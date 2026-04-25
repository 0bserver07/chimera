"""Tests for FileChange and ChangeType."""
from __future__ import annotations

from chimera.types import ChangeType, FileChange


class TestChangeType:
    def test_values(self) -> None:
        assert ChangeType.CREATE.value == "create"
        assert ChangeType.EDIT.value == "edit"
        assert ChangeType.DELETE.value == "delete"


class TestFileChange:
    def test_create_change(self) -> None:
        fc = FileChange(
            path="src/new.py",
            change_type=ChangeType.CREATE,
            after_content="print('hello')\n",
        )
        assert fc.path == "src/new.py"
        assert fc.change_type == ChangeType.CREATE
        assert fc.before_content is None
        assert fc.after_content == "print('hello')\n"

    def test_edit_change(self) -> None:
        fc = FileChange(
            path="src/old.py",
            change_type=ChangeType.EDIT,
            before_content="x = 1\n",
            after_content="x = 2\n",
        )
        assert fc.change_type == ChangeType.EDIT
        assert fc.before_content == "x = 1\n"
        assert fc.after_content == "x = 2\n"

    def test_delete_change(self) -> None:
        fc = FileChange(
            path="src/gone.py",
            change_type=ChangeType.DELETE,
            before_content="old content\n",
        )
        assert fc.change_type == ChangeType.DELETE
        assert fc.after_content is None

    def test_compute_diff_edit(self) -> None:
        before = "line1\nline2\nline3\n"
        after = "line1\nmodified\nline3\n"
        diff = FileChange.compute_diff("foo.py", before, after)
        assert "--- a/foo.py" in diff
        assert "+++ b/foo.py" in diff
        assert "-line2" in diff
        assert "+modified" in diff

    def test_compute_diff_create(self) -> None:
        diff = FileChange.compute_diff("new.py", "", "content\n")
        assert "+content" in diff

    def test_compute_diff_delete(self) -> None:
        diff = FileChange.compute_diff("old.py", "content\n", "")
        assert "-content" in diff

    def test_compute_diff_no_change(self) -> None:
        diff = FileChange.compute_diff("same.py", "same\n", "same\n")
        assert diff == ""

    def test_diff_field(self) -> None:
        before = "a = 1\n"
        after = "a = 2\n"
        diff = FileChange.compute_diff("x.py", before, after)
        fc = FileChange(
            path="x.py",
            change_type=ChangeType.EDIT,
            before_content=before,
            after_content=after,
            diff=diff,
        )
        assert fc.diff is not None
        assert "-a = 1" in fc.diff
        assert "+a = 2" in fc.diff
