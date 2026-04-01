"""Built-in slash commands shipped with chimera."""
from __future__ import annotations

import subprocess

from chimera.commands.types import LocalCommand


def _help_handler(args: str) -> str:
    return (
        "Available commands: /help, /clear, /compact, /cost, /diff, "
        "/status, /model, /memory, /undo, /revert, /exit"
    )


def _clear_handler(args: str) -> str:
    return "Conversation cleared."


def _compact_handler(args: str) -> str:
    return "Conversation compacted."


def _cost_handler(args: str) -> str:
    return "Session cost: $0.00"


def _exit_handler(args: str) -> str:
    return "Goodbye."


def _diff_handler(args: str) -> str:
    """Show git diff."""
    try:
        result = subprocess.run(
            ["git", "diff", "--stat"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout or "No changes"
    except Exception as e:
        return f"Error: {e}"


def _status_handler(args: str) -> str:
    """Show git status."""
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout or "Clean working tree"
    except Exception as e:
        return f"Error: {e}"


def _model_handler(args: str) -> str:
    """Show or switch model."""
    return "Current model: (use --model flag to switch)"


def _memory_handler(args: str) -> str:
    """Show persistent memory."""
    from pathlib import Path

    from chimera.core.memory import PersistentMemory

    mem = PersistentMemory(Path("."))
    content = mem.load()
    return content or "No persistent memory found"


def _undo_handler(args: str) -> str:
    """Undo the last turn's file changes."""
    return (
        "Undo requires an active snapshot manager. "
        "Use 'chimera code --preset claude_code' for snapshot support."
    )


def _revert_handler(args: str) -> str:
    """Revert files to a specific turn."""
    return (
        "Revert requires an active snapshot manager. "
        "Use 'chimera code --preset claude_code' for snapshot support."
    )


def get_builtin_commands() -> list[LocalCommand]:
    """Return the list of built-in local commands."""
    return [
        LocalCommand(
            name="help",
            description="Show available commands",
            aliases=["h", "?"],
            handler=_help_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="clear",
            description="Clear conversation history",
            handler=_clear_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="compact",
            description="Compact conversation to save context",
            handler=_compact_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="cost",
            description="Show session cost",
            handler=_cost_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="diff",
            description="Show git diff summary",
            handler=_diff_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="status",
            description="Show git status",
            aliases=["st"],
            handler=_status_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="model",
            description="Show or switch model",
            handler=_model_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="memory",
            description="Show persistent memory",
            aliases=["mem"],
            handler=_memory_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="undo",
            description="Undo the last turn's file changes",
            handler=_undo_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="revert",
            description="Revert files to a specific turn",
            argument_hint="<turn_number>",
            handler=_revert_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="exit",
            description="Exit the session",
            aliases=["quit", "q"],
            handler=_exit_handler,
            loaded_from="builtin",
        ),
    ]
