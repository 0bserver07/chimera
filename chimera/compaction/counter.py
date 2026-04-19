from __future__ import annotations

import json

from chimera.types import Message

try:
    import tiktoken  # type: ignore[import-not-found]  # optional dep
    _HAS_TIKTOKEN = True
except ImportError:
    tiktoken = None  # type: ignore[assignment]
    _HAS_TIKTOKEN = False


class TokenCounter:
    """Estimate token counts for text and message lists.

    When *tiktoken* is installed the given encoding model is used for
    precise counts.  Otherwise a character-based heuristic (``len(text) // 4``)
    is applied.
    """

    def __init__(self, model: str = "cl100k_base") -> None:
        self._model = model
        self._encoding: object | None = None
        if _HAS_TIKTOKEN:
            self._encoding = tiktoken.get_encoding(model)

    def count(self, text: str) -> int:
        """Return the estimated token count for *text*."""
        if self._encoding is not None:
            return len(self._encoding.encode(text))  # type: ignore[union-attr]
        return len(text) // 4

    def count_messages(self, messages: list[Message]) -> int:
        """Return the total estimated tokens across all *messages*.

        Counts each message's content as well as the serialised arguments
        of any tool calls attached to the message.
        """
        total = 0
        for msg in messages:
            total += self.count(msg.content)
            for tc in msg.tool_calls:
                total += self.count(json.dumps(tc.arguments))
        return total
