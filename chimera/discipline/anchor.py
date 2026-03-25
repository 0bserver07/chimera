"""Instruction anchoring to combat context drift.

Compaction-aware: checks whether instructions are still present in recent
context before injecting, avoiding duplicates.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.types import Message

__all__ = ["InstructionAnchor"]

_MARKER = "--- INSTRUCTION ANCHOR ---"


class InstructionAnchor:
    """Re-inject instructions every N turns to combat context drift.

    Compaction-aware: checks if instructions are still present
    in recent context before injecting (avoids duplicates).

    Attributes:
        instructions: List of instruction strings to anchor.
        interval: Inject every *interval* turns (default 10).
    """

    def __init__(self, instructions: list[str], interval: int = 10) -> None:
        self._instructions = instructions
        self._interval = interval

    def should_inject(self, turn_count: int, context: list[Message]) -> bool:
        """Return ``True`` if interval reached AND instructions not found in last 5 messages.

        Args:
            turn_count: Current turn number (1-based).
            context: Conversation message history.

        Returns:
            Whether to inject the anchor instructions.
        """
        if turn_count <= 0 or turn_count % self._interval != 0:
            return False

        # Check last 5 messages for marker presence.
        recent = context[-5:] if len(context) >= 5 else context
        for msg in recent:
            if _MARKER in msg.content:
                return False

        return True

    def get_injection(self) -> str:
        """Return newline-joined instructions with a marker header.

        Returns:
            Formatted injection string with marker and instructions.
        """
        body = "\n".join(self._instructions)
        return f"{_MARKER}\n{body}"
