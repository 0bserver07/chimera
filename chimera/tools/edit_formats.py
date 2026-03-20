"""Multiple coder strategies: support different edit formats.

Ported from Aider's edit-format concept. Supports whole-file, diff,
search-replace, and udiff formats. Lets the agent (or caller) pick the
best format for the task.

Each format is a codec: it can *render* a file change into a text block
the LLM sees, and *parse* the LLM's output back into concrete edits.
"""
from __future__ import annotations

import re
import textwrap
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class EditFormatType(Enum):
    """Supported edit format types."""
    WHOLE_FILE = "whole_file"
    SEARCH_REPLACE = "search_replace"
    DIFF = "diff"
    UDIFF = "udiff"


@dataclass
class FileEdit:
    """A single file edit parsed from LLM output.

    Args:
        path: File path.
        old_content: Original content (for search-replace / diff).
        new_content: New content to apply.
        is_create: Whether this creates a new file.
    """

    path: str
    old_content: str = ""
    new_content: str = ""
    is_create: bool = False


class EditFormat(ABC):
    """Base class for edit format codecs."""

    name: EditFormatType

    @abstractmethod
    def render(self, path: str, content: str) -> str:
        """Render a file's content in this edit format for the LLM."""

    @abstractmethod
    def parse(self, text: str) -> list[FileEdit]:
        """Parse LLM output into concrete file edits."""

    @abstractmethod
    def instructions(self) -> str:
        """Return instructions to include in the system prompt."""


class WholeFileFormat(EditFormat):
    """Whole-file replacement format: LLM outputs entire file contents."""

    name = EditFormatType.WHOLE_FILE

    def render(self, path: str, content: str) -> str:
        return f"```{path}\n{content}\n```"

    def parse(self, text: str) -> list[FileEdit]:
        edits: list[FileEdit] = []
        pattern = re.compile(r"```(\S+)\n(.*?)```", re.DOTALL)
        for m in pattern.finditer(text):
            edits.append(FileEdit(path=m.group(1), new_content=m.group(2)))
        return edits

    def instructions(self) -> str:
        return (
            "Return the COMPLETE file contents inside a fenced code block "
            "with the filename as the language tag: ```path/to/file.py"
        )


class SearchReplaceFormat(EditFormat):
    """Search/replace blocks: <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE."""

    name = EditFormatType.SEARCH_REPLACE

    def render(self, path: str, content: str) -> str:
        return f"File: {path}\n```\n{content}\n```"

    def parse(self, text: str) -> list[FileEdit]:
        edits: list[FileEdit] = []
        # Match search/replace blocks with optional file header
        pattern = re.compile(
            r"(?:(?:File|file):\s*(\S+)\n)?"
            r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE",
            re.DOTALL,
        )
        for m in pattern.finditer(text):
            edits.append(FileEdit(
                path=m.group(1) or "",
                old_content=m.group(2),
                new_content=m.group(3),
            ))
        return edits

    def instructions(self) -> str:
        return textwrap.dedent("""\
            Use search/replace blocks:
            File: path/to/file.py
            <<<<<<< SEARCH
            old code
            =======
            new code
            >>>>>>> REPLACE""")


class DiffFormat(EditFormat):
    """Standard unified diff format."""

    name = EditFormatType.DIFF

    def render(self, path: str, content: str) -> str:
        return f"File: {path}\n```\n{content}\n```"

    def parse(self, text: str) -> list[FileEdit]:
        edits: list[FileEdit] = []
        # Extract diff blocks
        pattern = re.compile(
            r"```diff\n(.*?)```", re.DOTALL,
        )
        for m in pattern.finditer(text):
            diff_text = m.group(1)
            # Extract filename from --- / +++ lines
            path_match = re.search(r"\+\+\+ [ab]/(.+)", diff_text)
            path = path_match.group(1) if path_match else ""
            edits.append(FileEdit(path=path, new_content=diff_text))
        return edits

    def instructions(self) -> str:
        return "Return changes as a unified diff in a ```diff code block."


class UdiffFormat(EditFormat):
    """Udiff (unified diff) with @@ hunk headers."""

    name = EditFormatType.UDIFF

    def render(self, path: str, content: str) -> str:
        return f"File: {path}\n```\n{content}\n```"

    def parse(self, text: str) -> list[FileEdit]:
        edits: list[FileEdit] = []
        pattern = re.compile(r"```udiff\n(.*?)```", re.DOTALL)
        for m in pattern.finditer(text):
            diff_text = m.group(1)
            path_match = re.search(r"\+\+\+ [ab]/(.+)", diff_text)
            path = path_match.group(1) if path_match else ""
            edits.append(FileEdit(path=path, new_content=diff_text))
        return edits

    def instructions(self) -> str:
        return "Return changes as a udiff in a ```udiff code block."


# Registry of all formats
FORMAT_REGISTRY: dict[EditFormatType, EditFormat] = {
    EditFormatType.WHOLE_FILE: WholeFileFormat(),
    EditFormatType.SEARCH_REPLACE: SearchReplaceFormat(),
    EditFormatType.DIFF: DiffFormat(),
    EditFormatType.UDIFF: UdiffFormat(),
}


def get_format(fmt: EditFormatType | str) -> EditFormat:
    """Get an edit format by type or string name.

    Args:
        fmt: An EditFormatType enum or its string value.

    Returns:
        The corresponding EditFormat instance.

    Raises:
        KeyError: If the format is not found.
    """
    if isinstance(fmt, str):
        fmt = EditFormatType(fmt)
    return FORMAT_REGISTRY[fmt]


def select_format(file_count: int, total_lines: int) -> EditFormat:
    """Auto-select the best edit format based on task complexity.

    Heuristic:
    - Single file, < 50 lines: whole file
    - Multiple files or large: search/replace
    - Very large diffs: udiff

    Args:
        file_count: Number of files being edited.
        total_lines: Total lines across all files.

    Returns:
        The recommended EditFormat.
    """
    if file_count == 1 and total_lines < 50:
        return FORMAT_REGISTRY[EditFormatType.WHOLE_FILE]
    if total_lines > 500:
        return FORMAT_REGISTRY[EditFormatType.UDIFF]
    return FORMAT_REGISTRY[EditFormatType.SEARCH_REPLACE]
