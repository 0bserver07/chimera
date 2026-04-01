"""Patch parser for the ``*** Begin Patch / *** End Patch`` format.

Supports update, add, and delete operations with multi-pass fuzzy
matching (exact -> rstrip -> trim -> normalized).
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class MatchPass(Enum):
    EXACT = "exact"
    RSTRIP = "rstrip"
    TRIM = "trim"
    NORMALIZED = "normalized"


@dataclass
class PatchHunk:
    context_before: list[str]
    removals: list[str]
    additions: list[str]
    context_after: list[str]


@dataclass
class FilePatch:
    path: str
    operation: str  # "update", "add", "delete"
    hunks: list[PatchHunk]


class PatchParser:
    """Parse and apply structured patch text."""

    def parse(self, patch_text: str) -> list[FilePatch]:
        """Parse ``*** Begin Patch`` / ``*** End Patch`` format.

        Returns a list of :class:`FilePatch` objects.
        """
        patches: list[FilePatch] = []
        lines = patch_text.strip().split("\n")
        i = 0

        # Skip to *** Begin Patch
        while i < len(lines) and "Begin Patch" not in lines[i]:
            i += 1
        i += 1  # Skip the Begin Patch line

        current_file: str | None = None
        current_op: str | None = None
        current_hunks: list[PatchHunk] = []
        current_context: list[str] = []
        current_removals: list[str] = []
        current_additions: list[str] = []

        while i < len(lines):
            line = lines[i]

            if "End Patch" in line:
                break

            if line.startswith("*** Update File:"):
                if current_file is not None:
                    self._flush_hunk(
                        current_hunks, current_context,
                        current_removals, current_additions,
                    )
                    patches.append(FilePatch(current_file, current_op, current_hunks))  # type: ignore[arg-type]
                current_file = line.split(":", 1)[1].strip()
                current_op = "update"
                current_hunks = []
                current_context = []
                current_removals = []
                current_additions = []

            elif line.startswith("*** Add File:"):
                if current_file is not None:
                    self._flush_hunk(
                        current_hunks, current_context,
                        current_removals, current_additions,
                    )
                    patches.append(FilePatch(current_file, current_op, current_hunks))  # type: ignore[arg-type]
                current_file = line.split(":", 1)[1].strip()
                current_op = "add"
                current_hunks = []
                current_context = []
                current_removals = []
                current_additions = []

            elif line.startswith("*** Delete File:"):
                if current_file is not None:
                    self._flush_hunk(
                        current_hunks, current_context,
                        current_removals, current_additions,
                    )
                    patches.append(FilePatch(current_file, current_op, current_hunks))  # type: ignore[arg-type]
                current_file = line.split(":", 1)[1].strip()
                current_op = "delete"
                current_hunks = []
                current_context = []
                current_removals = []
                current_additions = []

            elif line.startswith("-"):
                current_removals.append(line[1:])

            elif line.startswith("+"):
                current_additions.append(line[1:])

            else:
                # Context line — if we have pending removals/additions, flush
                if current_removals or current_additions:
                    self._flush_hunk(
                        current_hunks, current_context,
                        current_removals, current_additions,
                    )
                    current_context = []
                    current_removals = []
                    current_additions = []
                current_context.append(line.lstrip(" "))

            i += 1

        if current_file is not None:
            self._flush_hunk(
                current_hunks, current_context,
                current_removals, current_additions,
            )
            patches.append(FilePatch(current_file, current_op, current_hunks))  # type: ignore[arg-type]

        return patches

    def _flush_hunk(
        self,
        hunks: list[PatchHunk],
        context: list[str],
        removals: list[str],
        additions: list[str],
    ) -> None:
        if removals or additions:
            hunks.append(PatchHunk(
                context_before=list(context),
                removals=list(removals),
                additions=list(additions),
                context_after=[],
            ))
            context.clear()
            removals.clear()
            additions.clear()

    def apply(self, file_patch: FilePatch, content: str) -> str:
        """Apply a parsed :class:`FilePatch` to file content.

        Returns the new file content as a string.
        """
        if file_patch.operation == "delete":
            return ""

        if file_patch.operation == "add":
            all_lines: list[str] = []
            for hunk in file_patch.hunks:
                all_lines.extend(hunk.additions)
            return "\n".join(all_lines)

        # update
        for hunk in file_patch.hunks:
            content = self._apply_hunk(content, hunk)
        return content

    def _apply_hunk(self, content: str, hunk: PatchHunk) -> str:
        lines = content.split("\n")
        search = hunk.context_before + hunk.removals
        if not search:
            # No context — append additions
            return content + "\n" + "\n".join(hunk.additions)

        pos = self._find_match(lines, search, MatchPass.EXACT)
        if pos is None:
            pos = self._find_match(lines, search, MatchPass.RSTRIP)
        if pos is None:
            pos = self._find_match(lines, search, MatchPass.TRIM)
        if pos is None:
            pos = self._find_match(lines, search, MatchPass.NORMALIZED)

        if pos is None:
            raise ValueError("Could not find match for hunk")

        # Replace: remove context+removals, insert context+additions
        end = pos + len(search)
        new_lines = lines[:pos] + hunk.context_before + hunk.additions + lines[end:]
        return "\n".join(new_lines)

    def _find_match(
        self,
        lines: list[str],
        search: list[str],
        pass_type: MatchPass,
    ) -> int | None:
        norm = self._normalizer(pass_type)
        search_normed = [norm(s) for s in search]
        for i in range(len(lines) - len(search) + 1):
            window = [norm(lines[i + j]) for j in range(len(search))]
            if window == search_normed:
                return i
        return None

    def _normalizer(self, pass_type: MatchPass) -> Callable[[str], str]:
        if pass_type == MatchPass.EXACT:
            return lambda s: s
        elif pass_type == MatchPass.RSTRIP:
            return lambda s: s.rstrip()
        elif pass_type == MatchPass.TRIM:
            return lambda s: s.strip()
        else:  # NORMALIZED
            return lambda s: unicodedata.normalize("NFC", s.strip())
