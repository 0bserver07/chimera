"""Tests for chimera.commands.input_handler — user input processing."""
from __future__ import annotations

import pytest

from chimera.commands.input_handler import InputHandler
from chimera.commands.processor import SlashCommandProcessor
from chimera.commands.registry import CommandRegistry
from chimera.commands.types import LocalCommand


# ---------------------------------------------------------------------------
# Tests: no processor configured
# ---------------------------------------------------------------------------


class TestNoProcessor:
    @pytest.mark.asyncio
    async def test_no_processor_passes_through(self):
        """Without a processor, all input should pass through as not-a-command."""
        handler = InputHandler()
        was_command, output = await handler.process("/help")
        assert was_command is False
        assert output is None

    @pytest.mark.asyncio
    async def test_no_processor_normal_text(self):
        """Normal text should always pass through."""
        handler = InputHandler()
        was_command, output = await handler.process("hello world")
        assert was_command is False
        assert output is None


# ---------------------------------------------------------------------------
# Tests: with processor — slash commands
# ---------------------------------------------------------------------------


class TestWithProcessor:
    def _make_handler(self, commands: dict[str, str | None] = None) -> InputHandler:
        """Create a handler with a registry containing the given commands."""
        registry = CommandRegistry()
        for name, response in (commands or {}).items():
            registry.register(
                LocalCommand(
                    name=name,
                    description=f"Test {name}",
                    handler=lambda args, resp=response: resp or f"ran {name}",
                )
            )
        processor = SlashCommandProcessor(registry)
        return InputHandler(processor=processor)

    @pytest.mark.asyncio
    async def test_slash_command_detected(self):
        """A registered /command should be detected and executed."""
        handler = self._make_handler({"help": "Help output here"})
        was_command, output = await handler.process("/help")
        assert was_command is True
        assert output == "Help output here"

    @pytest.mark.asyncio
    async def test_unknown_slash_command(self):
        """An unregistered /command should return was_command=True with error."""
        handler = self._make_handler({})
        was_command, output = await handler.process("/nonexistent")
        assert was_command is True
        assert "Unknown command" in output

    @pytest.mark.asyncio
    async def test_normal_text_not_command(self):
        """Normal text should not be treated as a command."""
        handler = self._make_handler({"help": "Help text"})
        was_command, output = await handler.process("just chatting")
        assert was_command is False
        assert output is None

    @pytest.mark.asyncio
    async def test_slash_command_with_args(self):
        """Slash commands with arguments should pass args to handler."""
        registry = CommandRegistry()
        registry.register(
            LocalCommand(
                name="echo",
                description="Echo back args",
                handler=lambda args: f"echoed: {args}",
            )
        )
        processor = SlashCommandProcessor(registry)
        handler = InputHandler(processor=processor)

        was_command, output = await handler.process("/echo hello world")
        assert was_command is True
        assert output == "echoed: hello world"
