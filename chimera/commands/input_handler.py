"""User input handler -- detect slash commands or pass to model."""
from __future__ import annotations

from chimera.commands.processor import SlashCommandProcessor

__all__ = ["InputHandler"]


class InputHandler:
    """Process user input -- detect slash commands or pass to model."""

    def __init__(self, processor: SlashCommandProcessor | None = None) -> None:
        self._processor = processor

    async def process(self, user_input: str) -> tuple[bool, str | None]:
        """Returns ``(was_command, output)``.

        If ``was_command`` is ``True``, the caller should NOT send the
        input to the model.
        """
        if self._processor and user_input.startswith("/"):
            return await self._processor.process(user_input)
        return False, None
