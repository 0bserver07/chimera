from __future__ import annotations

import dataclasses

from chimera.types import Message

from chimera.compaction.base import CompactionStrategy


class PruneCompaction(CompactionStrategy):
    """Truncate oversized tool-result messages in place.

    For every ``tool`` message whose content exceeds *max_tool_output_lines*
    lines, the middle portion is replaced with a ``... [truncated] ...``
    marker while the first 20 and last 20 lines are preserved.
    """

    _HEAD_LINES: int = 20
    _TAIL_LINES: int = 20

    def __init__(self, max_tool_output_lines: int = 50) -> None:
        self.max_tool_output_lines = max_tool_output_lines

    def compact(self, messages: list[Message], budget: int) -> list[Message]:
        """Return a new list with long tool outputs truncated."""
        result: list[Message] = []
        for msg in messages:
            if msg.role == "tool" and self._is_too_long(msg.content):
                result.append(self._truncate(msg))
            else:
                result.append(msg)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_too_long(self, content: str) -> bool:
        return content.count("\n") + 1 > self.max_tool_output_lines

    def _truncate(self, msg: Message) -> Message:
        lines = msg.content.splitlines()
        head = lines[: self._HEAD_LINES]
        tail = lines[-self._TAIL_LINES :]
        truncated = "\n".join(head) + "\n... [truncated] ...\n" + "\n".join(tail)
        return dataclasses.replace(msg, content=truncated)
