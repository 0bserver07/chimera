"""Built-in slash commands shipped with chimera."""
from __future__ import annotations

import os
import subprocess
from typing import Any

from chimera.commands.types import LocalCommand


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COMMAND_CATEGORIES: dict[str, list[str]] = {
    "General": ["help", "clear", "compact", "cost", "exit"],
    "Session Management": ["session", "files", "history", "export"],
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
    """Clear the terminal screen (ANSI clear + cursor home).

    Conversation-history clear needs Session context this handler lacks;
    use the rich REPL for that.
    """
    # \033[2J = clear entire screen, \033[H = move cursor to top-left.
    return "\033[2J\033[H"


def _compact_handler(args: str) -> str:
    return (
        "Context compaction needs active Session state this handler can't reach.\n"
        "Use the rich REPL (`chimera code` without --preset) — it has working\n"
        "/compact, or call Session.compact() directly from Python."
    )


def _cost_handler(args: str) -> str:
    """Show cost from the most-recent session file on disk, if any."""
    import json
    from pathlib import Path

    session_dir = Path.home() / ".chimera" / "sessions"
    if not session_dir.exists():
        return "No cost data: no session files yet. Cost is tracked per session."
    files = sorted(
        session_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return "No cost data: no session files found."
    most_recent = files[0]
    total = 0.0
    turns = 0
    try:
        for line in most_recent.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Entries from the rich REPL include step_cost fields
            cost = entry.get("cost_usd") or entry.get("cost") or 0
            if isinstance(cost, (int, float)):
                total += float(cost)
                if cost > 0:
                    turns += 1
    except OSError as e:
        return f"Error reading {most_recent.name}: {e}"
    return (
        f"Most recent session: {most_recent.stem}\n"
        f"  cost:  ${total:.4f}\n"
        f"  turns: {turns}"
    )


def _exit_handler(args: str) -> str:
    """Actually exit the process."""
    raise SystemExit(0)


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
    """Show the active model (from env). Switching needs a new process.

    Reads ANTHROPIC_MODEL / OPENAI_MODEL env vars, same precedence
    create_provider() uses.
    """
    anthropic = os.environ.get("ANTHROPIC_MODEL")
    openai_ = os.environ.get("OPENAI_MODEL")

    if args.strip():
        return (
            "Mid-session model switching requires Provider state this handler\n"
            "can't reach. Exit and restart with: chimera code --model <name>"
        )

    lines = []
    if anthropic:
        lines.append(f"ANTHROPIC_MODEL: {anthropic}")
    if openai_:
        lines.append(f"OPENAI_MODEL: {openai_}")
    if not lines:
        lines.append("No model env vars set (ANTHROPIC_MODEL / OPENAI_MODEL).")
    return "\n".join(lines)


def _memory_handler(args: str) -> str:
    """Show persistent memory."""
    from pathlib import Path

    from chimera.core.memory import PersistentMemory

    mem = PersistentMemory(Path("."))
    content = mem.load()
    return content or "No persistent memory found"


def _undo_handler(args: str) -> str:
    """Best-effort undo: prefer chimera snapshot, fall back to git.

    A full snapshot manager needs Session context we don't have here.
    But we can still help: if the user is in a git repo with a clean
    working tree except for recent changes, offer `git checkout -- .`.
    """
    from pathlib import Path

    snapshot_dir = Path.cwd() / ".chimera" / "snapshots"
    if snapshot_dir.exists():
        snapshots = sorted(snapshot_dir.glob("*.json"))
        if snapshots:
            return (
                f"Found {len(snapshots)} snapshots in {snapshot_dir}.\n"
                "To restore, use the rich REPL's /checkpoint restore <name>\n"
                "(snapshot restore needs the CheckpointManager this handler "
                "can't reach)."
            )

    try:
        result = subprocess.run(
            ["git", "diff", "--stat"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return (
                "Uncommitted changes:\n"
                f"{result.stdout}\n"
                "To discard all of them (destructive!):\n"
                "  git checkout -- .    # unstaged\n"
                "  git reset HEAD       # unstage\n"
                "For chimera snapshot-based undo, use the rich REPL."
            )
    except (OSError, subprocess.SubprocessError):
        pass

    return (
        "Nothing to undo: no chimera snapshots and no uncommitted git changes.\n"
        "Snapshot support is available in the rich REPL "
        "(`chimera code` without --preset)."
    )


def _revert_handler(args: str) -> str:
    """Revert files to a specific turn — needs Session context.

    Offers concrete git alternative if args look like a commit-ish.
    """
    target = args.strip()
    if not target:
        return (
            "Usage: /revert <turn_id>  (requires snapshot manager — use rich REPL)\n"
            "Git alternative: `git checkout <commit>` to jump to a commit."
        )
    # Looks like a short sha? Offer to run git checkout.
    if target.isalnum() and 4 <= len(target) <= 40:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--verify", target],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return (
                    f"'{target}' is a valid git revision ({result.stdout.strip()[:12]}).\n"
                    f"To revert: git checkout {target}\n"
                    f"For chimera turn-based revert, use the rich REPL."
                )
        except (OSError, subprocess.SubprocessError):
            pass
    return (
        f"Cannot revert to '{target}' from here — needs Session context.\n"
        "Use the rich REPL (`chimera code` without --preset) for turn-based revert."
    )


# ---------------------------------------------------------------------------
# Session Management handlers
# ---------------------------------------------------------------------------

def _session_handler(args: str) -> str:
    """Session management: list sessions on disk, show info.

    Note: save/fork/resume need active Session state that this handler
    contract doesn't expose. Use the rich REPL (`chimera code` without
    `--preset`) or `Session.save()` directly for those.
    """
    from pathlib import Path

    parts = args.strip().split()
    sub = parts[0] if parts else "info"

    if sub == "info":
        return (
            "Session commands: /session list (show saved sessions)\n"
            "For save/fork/resume, use the rich REPL or Session API directly."
        )

    if sub == "list":
        session_dir = Path.home() / ".chimera" / "sessions"
        if not session_dir.exists():
            return "No sessions found (~/.chimera/sessions/ does not exist)."
        files = sorted(session_dir.glob("*.jsonl"))
        if not files:
            return "No sessions saved."
        lines = ["Saved sessions:"]
        for f in files:
            size_kb = f.stat().st_size / 1024
            lines.append(f"  {f.stem}  ({size_kb:.1f} KB)")
        return "\n".join(lines)

    if sub in ("save", "fork", "resume"):
        return (
            f"/session {sub} requires active Session state. "
            "Use the rich REPL (`chimera code` without --preset) "
            "or call Session.{sub}() directly from Python."
        ).replace("{sub}", sub)

    return f"Unknown /session subcommand: {sub}. Try: info, list"


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
    """Show readline command history (terminal history, not message history).

    For full message-history access, use the rich REPL's /history command,
    which has the Session context this handler lacks.
    """
    try:
        import readline
    except ImportError:
        return "Readline not available on this platform."

    limit = 20
    if args.strip().isdigit():
        limit = int(args.strip())
    length = readline.get_current_history_length()
    if length == 0:
        return "No command history yet."
    start = max(1, length - limit + 1)
    lines = ["Recent commands:"]
    for i in range(start, length + 1):
        item = readline.get_history_item(i)
        if item:
            lines.append(f"  {i:4d}  {item}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Development handlers
# ---------------------------------------------------------------------------

def _commit_handler(args: str) -> str:
    """Commit already-staged changes.

    Safety: does NOT run `git add -A` (would sweep in .env, credentials,
    untracked debug files). Caller must stage files themselves first.
    """
    # Check there's something staged
    try:
        diff = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return f"git error: {e}"

    if diff.returncode != 0:
        return f"git error: {diff.stderr.strip() or 'not a git repository?'}"
    staged = [f for f in diff.stdout.splitlines() if f.strip()]
    if not staged:
        return (
            "Nothing staged to commit. Stage changes first:\n"
            "  git add <file>...     # specific files (recommended)\n"
            "  git add -p            # review each hunk interactively\n"
            "Note: /commit will NOT run `git add -A` — too easy to sweep "
            "in .env and other secrets."
        )

    msg = args.strip() or "chimera: auto-commit"
    try:
        result = subprocess.run(
            ["git", "commit", "-m", msg],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return f"git error: {e}"

    if result.returncode == 0:
        return f"Committed {len(staged)} file(s): {result.stdout.strip()}"
    return f"Commit failed: {result.stderr.strip() or result.stdout.strip()}"


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
    """Show current env-level context info (what this handler CAN see).

    Full message-window stats need Session state; use the rich REPL.
    """
    lines = [
        f"CWD: {os.getcwd()}",
        f"ANTHROPIC_MODEL: {os.environ.get('ANTHROPIC_MODEL', '(not set)')}",
        f"OPENAI_MODEL:    {os.environ.get('OPENAI_MODEL', '(not set)')}",
    ]
    # If a .chimera project exists, show loaded config summary
    from pathlib import Path
    dotdir = Path.cwd() / ".chimera"
    if dotdir.exists():
        entries = sorted(dotdir.iterdir())
        lines.append(f".chimera/: {len(entries)} entries")
        for e in entries[:10]:
            lines.append(f"  {e.name}")
        if len(entries) > 10:
            lines.append(f"  ... +{len(entries) - 10} more")
    else:
        lines.append(".chimera/: (none)")
    lines.append("")
    lines.append(
        "For token-usage stats and message-window state, use the rich REPL."
    )
    return "\n".join(lines)


def _debug_handler(args: str) -> str:
    """Toggle CHIMERA_DEBUG env var (persists for child processes only).

    Affects subprocess-based tools and subsequent `create_provider` calls
    that read the var. Can't reach the live loop's debug state.
    """
    current = os.environ.get("CHIMERA_DEBUG", "")
    if args.strip().lower() in ("off", "0", "false"):
        os.environ.pop("CHIMERA_DEBUG", None)
        return "CHIMERA_DEBUG unset."
    if args.strip().lower() in ("on", "1", "true"):
        os.environ["CHIMERA_DEBUG"] = "1"
        return "CHIMERA_DEBUG=1."
    if current in ("1", "true", "yes"):
        os.environ.pop("CHIMERA_DEBUG", None)
        return "CHIMERA_DEBUG unset (was on)."
    os.environ["CHIMERA_DEBUG"] = "1"
    return "CHIMERA_DEBUG=1 (was off)."


def _verbose_handler(args: str) -> str:
    """Toggle CHIMERA_VERBOSE env var. Same caveats as /debug."""
    current = os.environ.get("CHIMERA_VERBOSE", "")
    if args.strip().lower() in ("off", "0", "false"):
        os.environ.pop("CHIMERA_VERBOSE", None)
        return "CHIMERA_VERBOSE unset."
    if args.strip().lower() in ("on", "1", "true"):
        os.environ["CHIMERA_VERBOSE"] = "1"
        return "CHIMERA_VERBOSE=1."
    if current in ("1", "true", "yes"):
        os.environ.pop("CHIMERA_VERBOSE", None)
        return "CHIMERA_VERBOSE unset (was on)."
    os.environ["CHIMERA_VERBOSE"] = "1"
    return "CHIMERA_VERBOSE=1 (was off)."


# ---------------------------------------------------------------------------
# Configuration handlers
# ---------------------------------------------------------------------------

def _permissions_handler(args: str) -> str:
    """Show permission rules from .chimera/settings.json if present."""
    import json
    from pathlib import Path

    settings = Path.cwd() / ".chimera" / "settings.json"
    if not settings.exists():
        return (
            "No .chimera/settings.json in the current directory.\n"
            "Create one with a 'permissions' block to configure rules. Example:\n"
            '  {"permissions": {"policy": "allow_list", "allow": ["read", "list"]}}'
        )
    try:
        data = json.loads(settings.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return f"Error reading {settings}: {e}"
    perms = data.get("permissions")
    if not perms:
        return f"{settings} has no 'permissions' block."
    return f"Permissions from {settings.name}:\n{json.dumps(perms, indent=2)}"


def _hooks_handler(args: str) -> str:
    """Show hooks from .chimera/settings.json if present."""
    import json
    from pathlib import Path

    settings = Path.cwd() / ".chimera" / "settings.json"
    if not settings.exists():
        return (
            "No .chimera/settings.json in the current directory.\n"
            "Create one with a 'hooks' block to configure lifecycle hooks."
        )
    try:
        data = json.loads(settings.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return f"Error reading {settings}: {e}"
    hooks = data.get("hooks")
    if not hooks:
        return f"{settings} has no 'hooks' block."
    return f"Hooks from {settings.name}:\n{json.dumps(hooks, indent=2)}"


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
    """List snapshots under .chimera/snapshots/ if present."""
    from pathlib import Path

    snap_dir = Path.cwd() / ".chimera" / "snapshots"
    if not snap_dir.exists():
        return (
            "No snapshots: .chimera/snapshots/ does not exist.\n"
            "Snapshots are created automatically by the rich REPL "
            "(`chimera code` without --preset) on each turn."
        )
    snapshots = sorted(snap_dir.glob("*"))
    if not snapshots:
        return f"No snapshots in {snap_dir}."
    lines = [f"Snapshots in {snap_dir}:"]
    for s in snapshots:
        size_kb = s.stat().st_size / 1024 if s.is_file() else 0
        lines.append(f"  {s.name}  ({size_kb:.1f} KB)")
    lines.append("")
    lines.append("Restore with /undo (last) or /revert <name> from the rich REPL.")
    return "\n".join(lines)


def _tokens_handler(args: str) -> str:
    """Estimate tokens for given text (or stdin-piped text).

    Uses a fast heuristic: ~4 chars/token for English. Not tokenizer-exact
    but useful as a ballpark.
    """
    text = args.strip()
    if not text:
        return (
            "Usage: /tokens <text>    (estimate tokens for text)\n"
            "Message-window totals need Session state — use the rich REPL."
        )
    # Heuristic: ~4 chars/token for English. More accurate than word count
    # for typical code/prose mix.
    char_count = len(text)
    word_count = len(text.split())
    est_tokens = max(1, char_count // 4)
    return (
        f"~{est_tokens} tokens  "
        f"({char_count} chars, {word_count} words, heuristic ~4 chars/token)"
    )


def _env_handler(args: str) -> str:
    lines = [
        f"CWD: {os.getcwd()}",
        f"ANTHROPIC_MODEL: {os.environ.get('ANTHROPIC_MODEL', 'not set')}",
        f"OPENAI_MODEL: {os.environ.get('OPENAI_MODEL', 'not set')}",
    ]
    return "\n".join(lines)


def _export_handler(args: str) -> str:
    """Export the most-recent on-disk session to HTML.

    Usage: /export              -> uses most-recent session file
           /export <slug>       -> uses ~/.chimera/sessions/<slug>.jsonl
           /export <slug> <out> -> writes to <out> instead of default
    """
    import json
    from pathlib import Path

    from chimera.core.html_export import export_session_html

    parts = args.split()
    session_dir = Path.home() / ".chimera" / "sessions"

    if not session_dir.exists():
        return (
            "No sessions to export: ~/.chimera/sessions/ does not exist.\n"
            "Run the rich REPL first to generate session data."
        )

    if parts:
        slug = parts[0]
        src = session_dir / f"{slug}.jsonl"
        if not src.exists():
            return f"Session not found: {src}"
        out_path = Path(parts[1]) if len(parts) > 1 else Path(f"{slug}.html")
    else:
        files = sorted(
            session_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            return "No session files found."
        src = files[0]
        out_path = Path(f"{src.stem}.html")

    # Parse the session file — it's JSONL with message entries
    messages: list[dict[str, Any]] = []
    try:
        for line in src.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Message entries have role/content; skip tree bookkeeping entries
            if "role" in entry and "content" in entry:
                messages.append(entry)
            elif "message" in entry and isinstance(entry["message"], dict):
                messages.append(entry["message"])
    except OSError as e:
        return f"Error reading {src}: {e}"

    if not messages:
        return f"{src.name} has no exportable messages."

    try:
        path = export_session_html(messages, out_path)
    except Exception as e:
        return f"Export failed: {e}"
    return f"Exported {len(messages)} messages from {src.name} to {path}"


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
        # --- Export ---
        LocalCommand(
            name="export",
            description="Export session as HTML",
            aliases=["html"],
            handler=_export_handler,
            loaded_from="builtin",
        ),
    ]
