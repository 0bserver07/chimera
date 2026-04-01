"""Built-in slash commands shipped with chimera."""
from __future__ import annotations

import os
import subprocess

from chimera.commands.types import LocalCommand


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COMMAND_CATEGORIES: dict[str, list[str]] = {
    "General": ["help", "clear", "compact", "cost", "exit"],
    "Session Management": ["session", "files", "history"],
    "Development": ["commit", "test"],
    "Git": ["diff", "status"],
    "Context": ["context", "debug", "verbose"],
    "Configuration": ["model", "memory", "permissions", "hooks", "preset", "plan"],
    "Information": ["version", "snapshot", "tokens", "env"],
    "Undo / Revert": ["undo", "revert"],
}


# ---------------------------------------------------------------------------
# Original handlers
# ---------------------------------------------------------------------------

def _help_handler(args: str) -> str:
    commands = get_builtin_commands()
    name_set = {c.name for c in commands if not c.is_hidden}

    lines: list[str] = ["Available commands:\n"]
    cmd_map = {c.name: c for c in commands}
    for category, names in _COMMAND_CATEGORIES.items():
        present = [n for n in names if n in name_set]
        if not present:
            continue
        lines.append(f"  {category}:")
        for n in present:
            cmd = cmd_map[n]
            alias_str = f" (aliases: {', '.join(cmd.aliases)})" if cmd.aliases else ""
            lines.append(f"    /{n:<14} {cmd.description}{alias_str}")
        lines.append("")
    return "\n".join(lines)


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


# ---------------------------------------------------------------------------
# Session Management handlers
# ---------------------------------------------------------------------------

def _session_handler(args: str) -> str:
    parts = args.strip().split()
    sub = parts[0] if parts else "info"
    if sub == "info":
        return "Session management: /session save [name] | /session list"
    return f"Session command '{sub}' not yet implemented"


def _files_handler(args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        files = result.stdout.strip()
        return files or "No modified files"
    except Exception:
        return "Git not available"


def _history_handler(args: str) -> str:
    return "Message history requires active session context"


# ---------------------------------------------------------------------------
# Development handlers
# ---------------------------------------------------------------------------

def _commit_handler(args: str) -> str:
    msg = args.strip() or "Auto-commit by chimera"
    try:
        subprocess.run(["git", "add", "-A"], capture_output=True, timeout=10)
        result = subprocess.run(
            ["git", "commit", "-m", msg],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout or result.stderr or "Committed"
    except Exception as e:
        return f"Error: {e}"


def _test_handler(args: str) -> str:
    cmd = args.strip() or "python -m pytest --tb=short -q"
    try:
        result = subprocess.run(
            cmd.split(),
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.stdout[-2000:] if result.stdout else result.stderr[-2000:] or "No output"
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Context handlers
# ---------------------------------------------------------------------------

def _context_handler(args: str) -> str:
    return "Context info requires active session. Use /tokens for estimation."


def _debug_handler(args: str) -> str:
    return "Debug mode toggled (requires session integration)"


def _verbose_handler(args: str) -> str:
    return "Verbose mode toggled (requires session integration)"


# ---------------------------------------------------------------------------
# Configuration handlers
# ---------------------------------------------------------------------------

def _permissions_handler(args: str) -> str:
    return "Permission rules: configure in .chimera/settings.json under 'permissions'"


def _hooks_handler(args: str) -> str:
    return "Hooks: configure in .chimera/settings.json under 'hooks'"


def _preset_handler(args: str) -> str:
    from chimera.assembly.presets import PRESETS

    lines = ["Available presets:"]
    for name, cfg in PRESETS.items():
        lines.append(
            f"  {name}: {cfg.description} (tools={cfg.tool_set}, max_turns={cfg.max_turns})"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Information handlers
# ---------------------------------------------------------------------------

def _version_handler(args: str) -> str:
    try:
        from chimera import __version__

        return f"chimera {__version__}"
    except ImportError:
        return "chimera (version unknown)"


def _snapshot_handler(args: str) -> str:
    return "Snapshots: use /undo to revert last turn, /revert <turn> for specific turn"


def _tokens_handler(args: str) -> str:
    return "Token estimation requires active session context"


def _env_handler(args: str) -> str:
    lines = [
        f"CWD: {os.getcwd()}",
        f"ANTHROPIC_MODEL: {os.environ.get('ANTHROPIC_MODEL', 'not set')}",
        f"OPENAI_MODEL: {os.environ.get('OPENAI_MODEL', 'not set')}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command list
# ---------------------------------------------------------------------------

def get_builtin_commands() -> list[LocalCommand]:
    """Return the list of built-in local commands."""
    return [
        # --- General ---
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
            name="exit",
            description="Exit the session",
            aliases=["quit", "q"],
            handler=_exit_handler,
            loaded_from="builtin",
        ),
        # --- Git ---
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
        # --- Session Management ---
        LocalCommand(
            name="session",
            description="Session management (save/list/resume)",
            aliases=["s"],
            handler=_session_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="files",
            description="Show files modified this session",
            handler=_files_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="history",
            description="Show recent messages",
            handler=_history_handler,
            loaded_from="builtin",
        ),
        # --- Development ---
        LocalCommand(
            name="commit",
            description="Stage and commit changes",
            handler=_commit_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="test",
            description="Run tests",
            handler=_test_handler,
            loaded_from="builtin",
        ),
        # --- Context ---
        LocalCommand(
            name="context",
            description="Show token usage and message count",
            handler=_context_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="debug",
            description="Toggle debug mode",
            handler=_debug_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="verbose",
            description="Toggle verbose output",
            handler=_verbose_handler,
            loaded_from="builtin",
        ),
        # --- Configuration ---
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
            name="permissions",
            description="Show permission rules",
            aliases=["perms"],
            handler=_permissions_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="hooks",
            description="Show active hooks",
            handler=_hooks_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="preset",
            description="Show current preset info",
            handler=_preset_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="plan",
            description="Toggle plan mode",
            aliases=["p"],
            handler=lambda args: (
                "Use the enter_plan_mode/exit_plan_mode tools to toggle plan mode."
            ),
            loaded_from="builtin",
        ),
        # --- Information ---
        LocalCommand(
            name="version",
            description="Show chimera version",
            handler=_version_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="snapshot",
            description="List file snapshots",
            aliases=["snap"],
            handler=_snapshot_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="tokens",
            description="Estimate token count of conversation",
            handler=_tokens_handler,
            loaded_from="builtin",
        ),
        LocalCommand(
            name="env",
            description="Show environment info (model, cwd, tools)",
            handler=_env_handler,
            loaded_from="builtin",
        ),
        # --- Undo / Revert ---
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
    ]
