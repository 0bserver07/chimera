"""Lightweight persistent memory backed by a Markdown file.

Provides :class:`PersistentMemory` which reads and writes a
``MEMORY.md`` file inside ``<project_dir>/.chimera/memory/``.
Content is truncated to 200 lines on load.
"""
from __future__ import annotations

from pathlib import Path
from chimera.config.paths import store_path

__all__ = ["PersistentMemory"]


class PersistentMemory:
    """File-backed memory for persisting notes across sessions.

    Args:
        project_dir: Root project directory.  Memory is stored at
            ``<project_dir>/.chimera/memory/MEMORY.md``.
    """

    def __init__(self, project_dir: str | Path) -> None:
        self._memory_dir = store_path("project-memory", project_dir)
        self._memory_file = self._memory_dir / "MEMORY.md"

    def load(self) -> str | None:
        """Read the memory file, truncating at 200 lines.

        Returns ``None`` if the file does not exist.
        """
        if not self._memory_file.exists():
            return None
        text = self._memory_file.read_text(encoding="utf-8")
        lines = text.splitlines()
        if len(lines) > 200:
            lines = lines[:200]
            return "\n".join(lines) + "\n... (truncated)"
        return "\n".join(lines)

    def write(self, content: str) -> None:
        """Create the memory directory and write *content* to the file."""
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._memory_file.write_text(content, encoding="utf-8")

    def append(self, content: str) -> None:
        """Append *content* to the memory file with a preceding newline."""
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        with self._memory_file.open("a", encoding="utf-8") as f:
            f.write("\n" + content)
