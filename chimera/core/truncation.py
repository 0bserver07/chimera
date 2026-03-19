"""Head+tail output truncation for tool results.

When tool output exceeds a threshold, preserve the first N and last M lines
with a truncation marker in between. Prevents context flooding while keeping
the most useful parts (setup at the top, errors at the bottom).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TruncationConfig:
    """Configuration for output truncation.

    Args:
        max_lines: Maximum total lines before truncation triggers.
        head_lines: Number of lines to keep from the start.
        tail_lines: Number of lines to keep from the end.
        marker: Text shown in place of truncated content.
    """

    max_lines: int = 200
    head_lines: int = 50
    tail_lines: int = 50
    marker: str = "[... {count} lines truncated ...]"


def truncate_output(text: str, config: TruncationConfig | None = None) -> str:
    """Truncate text using head+tail strategy.

    Args:
        text: The text to potentially truncate.
        config: Truncation settings. Uses defaults if None.

    Returns:
        The original text if under the limit, or a truncated version
        with head, marker, and tail.
    """
    if config is None:
        config = TruncationConfig()

    lines = text.split("\n")
    if len(lines) <= config.max_lines:
        return text

    head = lines[: config.head_lines]
    tail = lines[-config.tail_lines :]
    truncated_count = len(lines) - config.head_lines - config.tail_lines
    marker = config.marker.format(count=truncated_count)

    return "\n".join(head + [marker] + tail)


def truncate_result_output(output: str, max_lines: int = 200) -> tuple[str, int]:
    """Convenience: truncate and return (text, lines_removed).

    Args:
        output: Raw tool output.
        max_lines: Maximum lines before truncation.

    Returns:
        Tuple of (truncated_text, lines_removed). lines_removed is 0
        if no truncation occurred.
    """
    lines = output.split("\n")
    if len(lines) <= max_lines:
        return output, 0

    config = TruncationConfig(max_lines=max_lines)
    truncated = truncate_output(output, config)
    removed = len(lines) - config.head_lines - config.tail_lines
    return truncated, removed
