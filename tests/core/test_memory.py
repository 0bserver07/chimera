"""Tests for chimera.core.memory — PersistentMemory file-based storage."""
from __future__ import annotations

from pathlib import Path

import pytest

from chimera.core.memory import PersistentMemory


class TestPersistentMemory:

    def test_write_and_load(self, tmp_path: Path) -> None:
        """write() stores content; load() retrieves it."""
        mem = PersistentMemory(tmp_path)
        mem.write("Hello, world!")
        assert mem.load() == "Hello, world!"

    def test_append(self, tmp_path: Path) -> None:
        """append() adds content with a preceding newline."""
        mem = PersistentMemory(tmp_path)
        mem.write("Line 1")
        mem.append("Line 2")
        content = mem.load()
        assert content is not None
        assert "Line 1" in content
        assert "Line 2" in content

    def test_truncation_at_200_lines(self, tmp_path: Path) -> None:
        """load() truncates content to 200 lines and appends truncation notice."""
        mem = PersistentMemory(tmp_path)
        lines = [f"Line {i}" for i in range(300)]
        mem.write("\n".join(lines))
        content = mem.load()
        assert content is not None
        assert content.endswith("... (truncated)")
        result_lines = content.splitlines()
        # 200 content lines + 1 truncation notice line
        assert len(result_lines) == 201
