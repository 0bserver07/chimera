"""Sketch synthesis -- fill holes in partially-written source files.

A :class:`SketchSpec` is a :class:`Spec` built from source files containing
``# HOLE: <description>`` markers.  The agent receives the full file context
but is instructed to fill only the marked holes, preserving surrounding code.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from chimera.training.spec import Spec
from chimera.env.base import Environment

_HOLE_RE = re.compile(r"^\s*#\s*HOLE:\s*(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Hole:
    """A hole in a sketch that the agent must fill.

    Attributes:
        id: Unique numeric identifier across all sketch files.
        description: Human-readable description extracted from the marker.
        file_path: Path of the sketch file containing this hole.
        line: 1-based line number of the ``# HOLE:`` marker.
        indent: Leading whitespace of the marker line.
    """

    id: int
    description: str
    file_path: str
    line: int
    indent: str


class SketchSpec(Spec):
    """Spec created from source files with ``# HOLE`` markers.

    Parses files for ``# HOLE: <description>`` comments.  The agent
    receives the full file context but is instructed to only fill
    the marked holes.

    Args:
        files: Mapping of file path to file content (with HOLE markers).
        description: Optional human-readable description.  Defaults to a
            generic "fill the holes" message.
    """

    def __init__(self, files: dict[str, str], description: str | None = None) -> None:
        self._sketch_files = files
        self._holes: list[Hole] = []
        self._parse_holes()
        text = description or "Fill the marked holes in the provided code sketch."
        super().__init__(text)

    def _parse_holes(self) -> None:
        """Walk every sketch file and extract ``# HOLE:`` markers."""
        hole_id = 0
        for path, content in self._sketch_files.items():
            for i, line in enumerate(content.splitlines(), 1):
                m = _HOLE_RE.match(line)
                if m:
                    indent = line[: len(line) - len(line.lstrip())]
                    self._holes.append(
                        Hole(
                            id=hole_id,
                            description=m.group(1).strip(),
                            file_path=path,
                            line=i,
                            indent=indent,
                        )
                    )
                    hole_id += 1

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_file(cls, path: str, description: str | None = None) -> SketchSpec:
        """Load a single sketch file.

        Args:
            path: Filesystem path to the sketch source file.
            description: Optional description override.

        Returns:
            A :class:`SketchSpec` with holes parsed from the file.
        """
        with open(path) as f:
            return cls({path: f.read()}, description)

    @classmethod
    def from_directory(
        cls, path: str, pattern: str = "**/*.py", description: str | None = None
    ) -> SketchSpec:
        """Load all sketch files matching *pattern* under *path*.

        Args:
            path: Root directory to search.
            pattern: Glob pattern for matching sketch files.
            description: Optional description override.

        Returns:
            A :class:`SketchSpec` with holes parsed from every matching file.
        """
        import glob

        files: dict[str, str] = {}
        for p in glob.glob(os.path.join(path, pattern), recursive=True):
            with open(p) as f:
                files[p] = f.read()
        return cls(files, description)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def holes(self) -> list[Hole]:
        """All holes across all sketch files (defensive copy)."""
        return list(self._holes)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def to_prompt(self) -> str:
        """Render the sketch as a prompt listing files and numbered holes.

        Returns:
            A multi-line prompt string instructing the agent to fill only
            the marked holes without modifying surrounding code.
        """
        lines: list[str] = [self.text, "", "## Sketch Files", ""]
        for path, content in self._sketch_files.items():
            lines.append(f"### {path}")
            lines.append("```python")
            lines.append(content)
            lines.append("```")
            lines.append("")
        lines.append("## Holes to Fill")
        lines.append("")
        for h in self._holes:
            lines.append(f"- **Hole {h.id}** ({h.file_path}:{h.line}): {h.description}")
        lines.append("")
        lines.append("Fill ONLY the marked holes. Do not modify code outside the holes.")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Environment helpers
    # ------------------------------------------------------------------

    def write_sketches(self, env: Environment) -> None:
        """Write every sketch file into *env*.

        Args:
            env: Target execution environment.
        """
        for path, content in self._sketch_files.items():
            env.write_file(path, content)
