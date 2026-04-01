"""Tests for chimera.tools.multi_edit — Issue #122."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from chimera.tools.multi_edit import MultiEditTool


class TestMultiEditTool:
    """MultiEditTool applies multiple search-and-replace edits."""

    def test_multi_edit_single_file(self, tmp_path: Path):
        """Two edits in the same file are applied sequentially."""
        f = tmp_path / "hello.py"
        f.write_text("aaa\nbbb\nccc\n")

        tool = MultiEditTool()
        result = tool.execute(
            {
                "edits": [
                    {"file": str(f), "search": "aaa", "replace": "AAA"},
                    {"file": str(f), "search": "ccc", "replace": "CCC"},
                ],
            },
            env=None,
        )

        assert result.error is None
        assert "[1]" in result.output and "edited successfully" in result.output
        assert "[2]" in result.output and "edited successfully" in result.output
        assert f.read_text() == "AAA\nbbb\nCCC\n"

    def test_multi_edit_multiple_files(self, tmp_path: Path):
        """One edit each in two different files."""
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        a.write_text("alpha")
        b.write_text("beta")

        tool = MultiEditTool()
        result = tool.execute(
            {
                "edits": [
                    {"file": str(a), "search": "alpha", "replace": "ALPHA"},
                    {"file": str(b), "search": "beta", "replace": "BETA"},
                ],
            },
            env=None,
        )

        assert result.error is None
        assert "[1]" in result.output and "edited successfully" in result.output
        assert "[2]" in result.output and "edited successfully" in result.output
        assert a.read_text() == "ALPHA"
        assert b.read_text() == "BETA"

    def test_multi_edit_file_not_found(self, tmp_path: Path):
        """Edit targeting a nonexistent file reports file not found."""
        tool = MultiEditTool()
        result = tool.execute(
            {
                "edits": [
                    {"file": str(tmp_path / "nope.py"), "search": "x", "replace": "y"},
                ],
            },
            env=None,
        )

        assert result.error is None  # tool-level error is None; per-edit status in output
        assert "file not found" in result.output

    def test_multi_edit_search_not_found(self, tmp_path: Path):
        """Edit whose search text is absent reports search text not found."""
        f = tmp_path / "exists.py"
        f.write_text("hello world")

        tool = MultiEditTool()
        result = tool.execute(
            {
                "edits": [
                    {"file": str(f), "search": "MISSING", "replace": "x"},
                ],
            },
            env=None,
        )

        assert result.error is None
        assert "search text not found" in result.output
        # File unchanged
        assert f.read_text() == "hello world"
