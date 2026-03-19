"""Track files read and modified during agent execution."""
from __future__ import annotations

from dataclasses import dataclass, field

from chimera.compaction.base import CompactionMetadata


@dataclass
class FileTracker:
    """Tracks files read and modified during agent execution."""

    read_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    _seen_read: set[str] = field(default_factory=set)
    _seen_modified: set[str] = field(default_factory=set)

    def record_read(self, path: str) -> None:
        if path not in self._seen_read:
            self._seen_read.add(path)
            self.read_files.append(path)

    def record_modified(self, path: str) -> None:
        if path not in self._seen_modified:
            self._seen_modified.add(path)
            self.modified_files.append(path)

    def to_metadata(self) -> CompactionMetadata:
        return CompactionMetadata(
            read_files=list(self.read_files),
            modified_files=list(self.modified_files),
        )

    def to_prompt_section(self) -> str:
        if not self.read_files and not self.modified_files:
            return ""
        lines = ["## Files you've been working with"]
        if self.modified_files:
            lines.append("Modified: " + ", ".join(self.modified_files))
        if self.read_files:
            lines.append("Read: " + ", ".join(self.read_files))
        return "\n".join(lines)
