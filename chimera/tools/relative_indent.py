"""Relative Indenter: robust search/replace that handles indentation mismatches.

Ported from Aider's approach. When old_str doesn't match exactly because of
whitespace differences, normalizes indentation to relative levels and retries.
On match, applies the replacement with the *target's* original indentation
preserved.

This is a standalone utility (not a tool) that can be used by EditFileTool or
any other code needing indent-aware replacement.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass


@dataclass
class IndentMatch:
    """Result of an indent-aware match."""

    start: int
    end: int
    base_indent: str
    strategy: str  # "exact", "relative_indent"


def _get_indent(line: str) -> str:
    """Return the leading whitespace of a line."""
    return line[: len(line) - len(line.lstrip())]


def _relative_signature(text: str) -> list[tuple[int, str]]:
    """Convert text to (relative_indent_level, stripped_content) tuples.

    The first non-empty line is base 0. Subsequent lines are relative.
    Tabs are expanded to 4 spaces for comparison.
    """
    lines = text.expandtabs(4).splitlines()
    result: list[tuple[int, str]] = []
    base: int | None = None
    for line in lines:
        stripped = line.lstrip()
        if not stripped:
            result.append((0, ""))
            continue
        indent = len(line) - len(stripped)
        if base is None:
            base = indent
        result.append((indent - base, stripped))
    return result


def find_with_relative_indent(
    content: str,
    search: str,
) -> IndentMatch | None:
    """Find *search* in *content* using relative-indentation matching.

    First tries exact match. If that fails, normalizes both sides to relative
    indentation levels and scans for a window match.

    Args:
        content: The full file content to search within.
        search: The search string (potentially with wrong indentation).

    Returns:
        An IndentMatch with byte offsets into *content*, or None.
    """
    # Try exact match first
    idx = content.find(search)
    if idx != -1 and content.find(search, idx + 1) == -1:
        return IndentMatch(idx, idx + len(search), "", "exact")

    # Relative indent matching
    search_sig = _relative_signature(search)
    if not search_sig:
        return None
    content_lines = content.splitlines(keepends=True)
    n = len(search_sig)
    if n > len(content_lines):
        return None

    for i in range(len(content_lines) - n + 1):
        window_text = "".join(content_lines[i : i + n])
        window_sig = _relative_signature(window_text)
        if len(window_sig) != len(search_sig):
            continue
        if all(
            ws[0] == ss[0] and ws[1] == ss[1]
            for ws, ss in zip(window_sig, search_sig)
        ):
            start = sum(len(content_lines[j]) for j in range(i))
            end = start + len(window_text)
            # Determine the base indent of the matched region
            first_content_line = content_lines[i].expandtabs(4)
            base = _get_indent(first_content_line)
            return IndentMatch(start, end, base, "relative_indent")

    return None


def replace_with_relative_indent(
    content: str,
    old_str: str,
    new_str: str,
) -> str | None:
    """Replace *old_str* with *new_str* in *content*, adapting indentation.

    If *old_str* matches via relative indentation, the replacement text is
    re-indented to match the target location's indentation level.

    Args:
        content: Full file content.
        old_str: Text to search for (may have wrong indentation).
        new_str: Replacement text (will be re-indented to match).

    Returns:
        Updated content string, or None if no match found.
    """
    match = find_with_relative_indent(content, old_str)
    if match is None:
        return None

    if match.strategy == "exact":
        return content[: match.start] + new_str + content[match.end :]

    # Re-indent new_str to match the target location's base indent
    new_dedented = textwrap.dedent(new_str)
    new_lines = new_dedented.splitlines(keepends=True)
    re_indented: list[str] = []
    for line in new_lines:
        if line.strip():
            re_indented.append(match.base_indent + line)
        else:
            re_indented.append(line)
    adjusted = "".join(re_indented)

    return content[: match.start] + adjusted + content[match.end :]
