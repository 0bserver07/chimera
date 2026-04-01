"""Slash-command processor for user input."""
from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING

from chimera.commands.registry import CommandRegistry
from chimera.commands.types import LocalCommand

if TYPE_CHECKING:
    from chimera.hooks.emitter import HookEmitter


class SlashCommandProcessor:
    """Detects ``/command`` input and dispatches to :class:`LocalCommand` handlers."""

    def __init__(
        self,
        registry: CommandRegistry,
        emitter: HookEmitter | None = None,
    ) -> None:
        self._registry = registry
        self._emitter = emitter

    async def process(self, user_input: str) -> tuple[bool, str | None]:
        """Process user input, handling slash commands.

        Returns:
            ``(True, output)`` if a slash command was matched and executed.
            ``(False, None)`` if the input is not a slash command.
        """
        # Fire USER_PROMPT_SUBMIT hook
        if self._emitter:
            from chimera.hooks.events import HookEvent

            result = await self._emitter.emit(
                HookEvent.USER_PROMPT_SUBMIT, user_prompt=user_input,
            )
            if not result.continue_execution:
                return True, result.stop_reason or "Blocked by hook"

        stripped = user_input.strip()
        if not stripped.startswith("/"):
            return False, None

        parts = stripped[1:].split(None, 1)
        command_name = parts[0] if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        command = self._registry.find(command_name)
        if command is None:
            return True, f"Unknown command: /{command_name}"

        if not isinstance(command, LocalCommand):
            # PromptCommands are handled by the agent loop, not here.
            return False, None

        result = command.handler(args)
        if inspect.isawaitable(result):
            result = await result

        return True, str(result)
