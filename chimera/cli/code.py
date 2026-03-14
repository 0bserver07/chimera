"""Interactive coding REPL with readline, slash commands, and session management."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable

from chimera import __version__
from chimera.core.agent import Agent
from chimera.core.loop import ReAct, drain_steps
from chimera.core.loop_config import LoopConfig
from chimera.core.prompt import Prompt
from chimera.core.tool_group import DEFAULT_TOOLS
from chimera.env.local import LocalEnvironment
from chimera.providers.cost_tracker import CostTracker
from chimera.providers.factory import create_provider
from chimera.sessions.session import Session
from chimera.streaming.handlers import ConsoleStreamHandler

_DEFAULT_SYSTEM = """\
You are a coding assistant with access to tools for reading, writing, \
editing files, running commands, searching code, and running tests. \
Help the user with their coding tasks. Be concise and direct."""

# -- Type aliases for command handlers --
PrintFn = Callable[[str], None]
CommandHandler = Callable[[Any, Any, str, PrintFn], None]


# -- Slash Command Handlers --

def cmd_help(session: Any, env: Any, args: str, out: PrintFn) -> None:
    out("Available commands:")
    for name in sorted(_COMMANDS):
        out(f"  /{name}")


def cmd_model(session: Any, env: Any, args: str, out: PrintFn) -> None:
    out(f"Current model: {session.provider.model_name}")


def cmd_cost(session: Any, env: Any, args: str, out: PrintFn) -> None:
    tracker = getattr(session, "cost_tracker", None)
    if tracker is None:
        out("No cost tracker active.")
        return
    out(f"Total cost: ${tracker.total:.4f}")
    bd = tracker.breakdown()
    if bd:
        out("Breakdown:")
        for model, cost in sorted(bd.items()):
            out(f"  {model}: ${cost:.4f}")
    if tracker.remaining is not None:
        out(f"Remaining budget: ${tracker.remaining:.4f}")


def cmd_clear(session: Any, env: Any, args: str, out: PrintFn) -> None:
    session.clear()
    out("Context cleared.")


def cmd_tools(session: Any, env: Any, args: str, out: PrintFn) -> None:
    tools = getattr(session, "tools", [])
    if not tools:
        out("No tools loaded.")
        return
    for t in tools:
        out(f"  {t.name}: {t.description[:60]}")


def cmd_context(session: Any, env: Any, args: str, out: PrintFn) -> None:
    ctx = getattr(session, "context", None)
    if ctx is None:
        out("No context available.")
        return
    msgs = ctx.to_messages()
    out(f"Messages: {len(msgs)}")
    total_chars = sum(len(m.content) for m in msgs)
    out(f"Estimated tokens: ~{total_chars // 4}")


def cmd_debug(session: Any, env: Any, args: str, out: PrintFn) -> None:
    current = getattr(session, "debug", False)
    session.debug = not current
    out(f"Debug mode: {'on' if session.debug else 'off'}")


def cmd_history(session: Any, env: Any, args: str, out: PrintFn) -> None:
    ctx = getattr(session, "context", None)
    if ctx is None:
        out("No context.")
        return
    msgs = ctx.to_messages()
    for m in msgs[-10:]:
        prefix = m.role.upper()[:4]
        content = m.content[:80].replace("\n", " ")
        out(f"  [{prefix}] {content}")


def cmd_compact(session: Any, env: Any, args: str, out: PrintFn) -> None:
    if hasattr(session, "compact"):
        session.compact()
        out("Context compacted.")
    else:
        out("Compaction not available.")


def cmd_audit(session: Any, env: Any, args: str, out: PrintFn) -> None:
    audit_log = getattr(session, "audit_log", None)
    if audit_log is None:
        out("No audit log active.")
        return
    if args.strip() == "clear":
        audit_log.clear()
        out("Audit log cleared.")
    else:
        summary = audit_log.summary()
        if not summary:
            out("Audit log is empty.")
        else:
            out("Audit summary:")
            for decision, count in sorted(summary.items()):
                out(f"  {decision}: {count}")


def cmd_checkpoint(session: Any, env: Any, args: str, out: PrintFn) -> None:
    manager = getattr(session, "checkpoint_manager", None)
    if manager is None:
        out("No checkpoint manager active.")
        return
    parts = args.strip().split(maxsplit=1)
    sub = parts[0] if parts else "list"

    if sub == "save":
        name = parts[1] if len(parts) > 1 else ""
        info = manager.create(name=name)
        out(f"Checkpoint saved: {info.name} ({info.id})")
    elif sub == "list":
        checkpoints = manager.list_checkpoints()
        if not checkpoints:
            out("No checkpoints.")
        else:
            for cp in checkpoints:
                out(f"  {cp.id}  {cp.name}  {cp.time_str}")
    elif sub == "restore":
        name = parts[1] if len(parts) > 1 else ""
        if not name:
            out("Usage: /checkpoint restore <name>")
            return
        try:
            info = manager.restore_by_name(name)
            out(f"Restored to checkpoint: {info.name}")
        except KeyError as exc:
            out(str(exc))
    elif sub == "undo":
        info = manager.undo()
        if info:
            out(f"Undone to checkpoint: {info.name}")
        else:
            out("No checkpoints to undo.")
    else:
        out(f"Unknown checkpoint command: {sub}")


def cmd_agent(session: Any, env: Any, args: str, out: PrintFn) -> None:
    parts = args.strip().split(maxsplit=1)
    sub = parts[0] if parts else "list"

    if sub == "list":
        try:
            from chimera.agents.loader import create_default_registry
            registry = create_default_registry()
            names = registry.list()
            out("Available agent presets:")
            for name in names:
                out(f"  {name}")
        except Exception as exc:
            out(f"Error loading agent presets: {exc}")
    elif sub == "set":
        out("Agent preset switching not yet supported in REPL.")
    else:
        out(f"Unknown agent command: {sub}")


def cmd_session(session: Any, env: Any, args: str, out: PrintFn) -> None:
    parts = args.strip().split(maxsplit=1)
    sub = parts[0] if parts else "list"

    if sub == "save":
        name = parts[1] if len(parts) > 1 else None
        if hasattr(session, "save"):
            session.save(name)
            out(f"Session saved{f' as {name}' if name else ''}.")
        else:
            out("Session save not available.")
    elif sub == "list":
        out("Session management: /session save [name] | /session list")
    elif sub == "fork":
        if hasattr(session, "fork"):
            session.fork()
            out("Session forked.")
        else:
            out("Session fork not available.")
    else:
        out(f"Unknown session command: {sub}")


def cmd_exit(session: Any, env: Any, args: str, out: PrintFn) -> None:
    raise SystemExit(0)


def cmd_init(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """Analyze the working directory and generate a project summary."""
    workdir = getattr(env, "workdir", os.getcwd())
    out(f"Analyzing {workdir}...")

    init_prompt = (
        "Analyze this project directory and provide a concise summary. "
        "List: 1) Project type and language, 2) Key files and structure, "
        "3) Build/test commands, 4) Any configuration files found. "
        "Be brief and factual."
    )

    try:
        result = drain_steps(session.iter_chat(init_prompt))
        out(f"\n  [cost: ${result.cost:.4f}]")
    except Exception as exc:
        out(f"Error during init: {exc}")


def cmd_yolo(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """Toggle auto-approve mode for tool calls."""
    # Use a simple session-level flag
    current = getattr(session, "_yolo_mode", False)
    session._yolo_mode = not current

    if session._yolo_mode:
        # Store original permission policy and swap to auto-approve
        from chimera.permissions.presets import AutoApprove
        agent = getattr(session, "agent", None)
        if agent and hasattr(agent, "loop") and hasattr(agent.loop, "config"):
            config = agent.loop.config
            if config:
                session._original_policy = getattr(config, "permissions", None)
                config.permissions = AutoApprove()
        out("YOLO mode ON — all tool calls auto-approved. Use /yolo again to disable.")
    else:
        # Restore original policy
        agent = getattr(session, "agent", None)
        if agent and hasattr(agent, "loop") and hasattr(agent.loop, "config"):
            config = agent.loop.config
            if config and hasattr(session, "_original_policy"):
                config.permissions = session._original_policy
        out("YOLO mode OFF — tool calls require approval again.")


# -- Command Registry --

_COMMANDS: dict[str, CommandHandler] = {
    "help": cmd_help,
    "model": cmd_model,
    "cost": cmd_cost,
    "clear": cmd_clear,
    "history": cmd_history,
    "tools": cmd_tools,
    "context": cmd_context,
    "debug": cmd_debug,
    "session": cmd_session,
    "compact": cmd_compact,
    "audit": cmd_audit,
    "checkpoint": cmd_checkpoint,
    "agent": cmd_agent,
    "exit": cmd_exit,
    "quit": cmd_exit,
    "init": cmd_init,
    "yolo": cmd_yolo,
}


def _dispatch_command(
    line: str, session: Any, env: Any, out: PrintFn,
) -> bool:
    """Dispatch a slash command. Returns True if handled."""
    if not line.startswith("/"):
        return False
    parts = line[1:].split(maxsplit=1)
    cmd_name = parts[0] if parts else ""
    args = parts[1] if len(parts) > 1 else ""

    handler = _COMMANDS.get(cmd_name)
    if handler is None:
        out(f"Unknown command: /{cmd_name}. Type /help for available commands.")
        return False

    handler(session, env, args, out)
    return True


# -- Tab Completion --

_COMMAND_NAMES = sorted(f"/{name}" for name in _COMMANDS)


def _complete_command(text: str, state: int) -> str | None:
    """Readline completer for slash commands."""
    matches = [c for c in _COMMAND_NAMES if c.startswith(text)]
    if state < len(matches):
        return matches[state]
    return None


# -- Readline Setup --

def _setup_readline() -> None:
    """Set up readline with history and tab completion."""
    try:
        import readline
    except ImportError:
        return

    history_dir = Path.home() / ".chimera"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_file = history_dir / "history"

    try:
        readline.read_history_file(str(history_file))
    except FileNotFoundError:
        pass

    readline.set_history_length(1000)
    readline.set_completer(_complete_command)
    readline.parse_and_bind("tab: complete")

    import atexit
    atexit.register(readline.write_history_file, str(history_file))


# -- Main REPL --

def run_code(args: Any) -> int:
    """Run the interactive coding REPL."""
    workdir = os.path.abspath(getattr(args, "workdir", None) or os.getcwd())
    provider = create_provider(model=getattr(args, "model", None))
    env = LocalEnvironment(workdir=workdir)
    env.setup()

    # Best-effort MCP tool loading
    try:
        mcp_config_path = Path.home() / ".chimera" / "mcp.json"
        if mcp_config_path.exists():
            import json
            mcp_config = json.loads(mcp_config_path.read_text())
            from chimera.mcp.tools import MCPToolSource
            mcp_tools = MCPToolSource.from_config(mcp_config)
            # tools would be added here
    except Exception:
        pass

    # Auto-discover project context
    system = _DEFAULT_SYSTEM
    try:
        from chimera.config.loader import ProjectConfig
        project = ProjectConfig.from_directory(workdir)
        if project and project.rules_text:
            system += "\n\n# Project Context\n" + project.rules_text
    except Exception:
        pass  # Config discovery is best-effort

    handler = ConsoleStreamHandler()
    cost_tracker = CostTracker()
    max_steps = getattr(args, "max_steps", 50) or 50
    config = LoopConfig(handler=handler, cost_tracker=cost_tracker)
    loop = ReAct(max_steps=max_steps, config=config)

    prompt = Prompt.from_string(system)
    agent = Agent(provider=provider, tools=list(DEFAULT_TOOLS), loop=loop, prompt=prompt)
    session = Session(agent=agent, env=env)
    session.provider = provider  # type: ignore[attr-defined]
    session.cost_tracker = cost_tracker  # type: ignore[attr-defined]
    session.tools = list(DEFAULT_TOOLS)  # type: ignore[attr-defined]
    session.debug = False  # type: ignore[attr-defined]

    _setup_readline()

    print(f"chimera code v{__version__} | model: {provider.model_name} | /help for commands")
    total_cost = 0.0

    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue

        # Slash command dispatch
        if user_input.startswith("/"):
            try:
                _dispatch_command(user_input, session, env, print)
            except SystemExit:
                print("Bye!")
                break
            continue

        # Regular chat
        try:
            result = drain_steps(session.iter_chat(user_input))
            total_cost += result.cost
            print(f"\n  [cost: ${result.cost:.4f} | steps: {result.steps}]")
        except KeyboardInterrupt:
            print("\n  (interrupted)")
        except Exception as exc:
            print(f"\n  Error: {exc}", file=sys.stderr)

    print(f"\nTotal cost: ${total_cost:.4f}")
    env.cleanup()
    return 0
