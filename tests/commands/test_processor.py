"""Tests for chimera.commands.processor — Phase 7."""
from __future__ import annotations

import asyncio

from chimera.commands.processor import SlashCommandProcessor
from chimera.commands.registry import CommandRegistry
from chimera.commands.types import LocalCommand


class TestSlashCommandProcessor:
    """SlashCommandProcessor routes /commands to LocalCommand handlers."""

    def _make_processor(self) -> SlashCommandProcessor:
        reg = CommandRegistry()
        reg.register(LocalCommand(
            name="help",
            description="Show help",
            handler=lambda args: "help output",
        ))
        return SlashCommandProcessor(reg)

    def test_processes_slash_command(self):
        proc = self._make_processor()
        handled, output = asyncio.run(proc.process("/help"))
        assert handled is True
        assert output == "help output"

    def test_non_slash_passes_through(self):
        proc = self._make_processor()
        handled, output = asyncio.run(proc.process("just a normal message"))
        assert handled is False
        assert output is None

    def test_unknown_command(self):
        proc = self._make_processor()
        handled, output = asyncio.run(proc.process("/nonexistent"))
        assert handled is True
        assert output is not None
        assert "Unknown command" in output
