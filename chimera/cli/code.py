"""Interactive coding REPL with readline, slash commands, and session management."""
from __future__ import annotations

import hashlib
import os
import select
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from chimera import __version__
from chimera.core.agent import Agent
from chimera.core.cancellation import CancellationToken
from chimera.core.file_tracker import FileTracker
from chimera.core.loop import ReAct, drain_steps
from chimera.core.loop_config import LoopConfig
from chimera.core.message_queue import MessageQueues
from chimera.core.prompt import Prompt
from chimera.core.tool_group import AGENT_TOOLS
from chimera.env.local import LocalEnvironment
from chimera.providers.cost_tracker import CostTracker
from chimera.providers.factory import create_provider
from chimera.sessions.session import Session
from chimera.sessions.tree import SessionTree
from chimera.streaming.handlers import ConsoleStreamHandler
from chimera.types import Message

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
    parts = args.strip().split()
    sub = parts[0] if parts else ""
    model_list = getattr(session, "_model_list", [])
    model_index = getattr(session, "_model_index", 0)

    if sub == "next" and model_list:
        model_index = (model_index + 1) % len(model_list)
        new_model = model_list[model_index]
        session.provider = create_provider(model=new_model)
        session._agent.provider = session.provider
        session._model_index = model_index
        out(f"Switched to: {new_model}")
    elif sub == "prev" and model_list:
        model_index = (model_index - 1) % len(model_list)
        new_model = model_list[model_index]
        session.provider = create_provider(model=new_model)
        session._agent.provider = session.provider
        session._model_index = model_index
        out(f"Switched to: {new_model}")
    else:
        out(f"Current model: {session.provider.model_name}")
        if len(model_list) > 1:
            out(f"Available: {', '.join(model_list)}")
            out("Use /model next or /model prev to cycle")


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
        out(
            "Mid-session agent switching isn't supported — Agent + tools + loop\n"
            "are wired at startup. To use a different preset, exit and run:\n"
            "  chimera code --preset <name>"
        )
    else:
        out(f"Unknown agent command: {sub}. Try: /agent list")


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
        from pathlib import Path

        session_dir = Path.home() / ".chimera" / "sessions"
        if not session_dir.exists():
            out("No sessions (~/.chimera/sessions/ does not exist).")
            return
        files = sorted(session_dir.glob("*.jsonl"))
        if not files:
            out("No sessions saved yet.")
            return
        out(f"Saved sessions ({len(files)}):")
        for f in files:
            size_kb = f.stat().st_size / 1024
            out(f"  {f.stem}  ({size_kb:.1f} KB)")
    elif sub == "fork":
        if hasattr(session, "fork"):
            session.fork()
            out("Session forked.")
        else:
            out("Session fork not available.")
    else:
        out(f"Unknown session command: {sub}. Try: save, list, fork")


def cmd_tree(session: Any, env: Any, args: str, out: PrintFn) -> None:
    tree = getattr(session, "_tree", None)
    if tree is None:
        out("No session tree active.")
        return
    leaves = tree.get_leaves()
    branch_points = tree.get_branch_points()
    out(f"Session tree: {tree.entry_count} entries, {len(leaves)} leaves, {len(branch_points)} branch points")
    out(f"Active leaf: {tree.active_leaf}")
    for leaf in leaves:
        marker = " <- active" if leaf == tree.active_leaf else ""
        branch = tree.get_branch(leaf)
        msg_count = sum(1 for e in branch if hasattr(e, 'message') and getattr(e, 'message', None) is not None)
        out(f"  {leaf[:8]}... ({msg_count} messages){marker}")


def cmd_branch(session: Any, env: Any, args: str, out: PrintFn) -> None:
    tree = getattr(session, "_tree", None)
    if tree is None:
        out("No session tree active.")
        return
    entry_id = args.strip()
    if not entry_id:
        out("Usage: /branch <entry_id>")
        return
    try:
        tree.fork(entry_id)
        out(f"Branched from {entry_id[:8]}...")
    except ValueError as e:
        out(str(e))


def cmd_switch(session: Any, env: Any, args: str, out: PrintFn) -> None:
    tree = getattr(session, "_tree", None)
    if tree is None:
        out("No session tree active.")
        return
    leaf_id = args.strip()
    if not leaf_id:
        out("Usage: /switch <leaf_id>")
        return
    try:
        session.switch_branch(leaf_id)
        out(f"Switched to branch {leaf_id[:8]}...")
    except (ValueError, AttributeError) as e:
        out(str(e))


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
    "tree": cmd_tree,
    "branch": cmd_branch,
    "switch": cmd_switch,
}


def _dispatch_command(
    line: str, session: Any, env: Any, out: PrintFn,
) -> bool:
    """Dispatch a slash command via :mod:`chimera.cli.slash_commands`.

    Kept as a thin wrapper for backwards compatibility with callers that
    imported this name directly. Returns True if handled.
    """
    from chimera.cli.slash_commands import dispatch as _shared_dispatch

    return _shared_dispatch(line, session, env, out)


# -- Tab Completion --


def _command_names() -> list[str]:
    """Return tab-completion candidates from the shared registry (and legacy dict)."""
    legacy = {f"/{name}" for name in _COMMANDS}
    try:
        from chimera.cli.slash_commands import COMMAND_NAMES
        return sorted(set(COMMAND_NAMES) | legacy)
    except ImportError:
        return sorted(legacy)


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
    except (FileNotFoundError, OSError):
        pass

    readline.set_history_length(1000)
    readline.set_completer(_complete_command)
    readline.parse_and_bind("tab: complete")

    import atexit
    atexit.register(readline.write_history_file, str(history_file))


# -- Session Path --

def _session_path(workdir: str) -> Path:
    """Stable session file path based on workdir hash."""
    h = hashlib.sha256(workdir.encode()).hexdigest()[:12]
    session_dir = Path.home() / ".chimera" / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir / f"{h}.jsonl"


def _read_steering_input() -> str | None:
    """Non-blocking read from stdin. Returns line or None."""
    try:
        readable, _, _ = select.select([sys.stdin], [], [], 0.1)
        if readable:
            line = sys.stdin.readline()
            return line.strip() if line else None
    except (OSError, ValueError):
        pass
    return None


# -- Main REPL --

def run_code(args: Any) -> int:
    """Run the interactive coding REPL."""
    mode = getattr(args, "mode", "interactive")
    if mode == "rpc":
        return _run_rpc_mode(args)
    if mode == "json":
        return _run_json_mode(args)

    # New CodingAgent stack — activated by --preset
    preset = getattr(args, "preset", None)
    if preset:
        import asyncio
        model = getattr(args, "model", None) or os.environ.get(
            "ANTHROPIC_MODEL", "claude-sonnet-4-20250514",
        )
        cwd = os.path.abspath(getattr(args, "workdir", None) or os.getcwd())

        # Non-interactive -p mode
        print_task = getattr(args, "print_mode", None)
        if print_task:
            from chimera.assembly.coding_agent import CodingAgent
            from chimera.core.loop_events import LoopEventType

            async def _print_run() -> None:
                agent = CodingAgent(model=model, preset=preset, project_dir=cwd)
                async for event in agent.run(print_task):
                    if event.type == LoopEventType.assistant:
                        content = getattr(event.data, 'content', str(event.data))
                        if content.strip():
                            print(content)
                    elif event.type == LoopEventType.assistant_chunk:
                        print(str(event.data), end="", flush=True)

            asyncio.run(_print_run())
            return 0

        asyncio.run(_run_new_stack(model=model, preset=preset, cwd=cwd))
        return 0

    workdir = os.path.abspath(getattr(args, "workdir", None) or os.getcwd())
    try:
        provider = create_provider(model=getattr(args, "model", None))
    except ValueError as e:
        print(f"Error: {e}")
        print("\nSet up a provider:")
        print("  export ANTHROPIC_API_KEY='sk-ant-...'")
        print("  # or for compatible endpoints:")
        print("  export ANTHROPIC_BASE_URL='https://...'")
        print("  export ANTHROPIC_AUTH_TOKEN='your-token'")
        print("  export ANTHROPIC_MODEL='model-name'")
        return 1
    env = LocalEnvironment(workdir=workdir)
    env.setup()

    # Best-effort MCP tool loading
    # WHY (audit B-6): previously the MCP tools were loaded then
    # discarded with `# noqa: F841`. Now we surface them through a local
    # variable consumed when building the tool list a few dozen lines down.
    mcp_extra_tools: list[Any] = []
    try:
        mcp_config_path = Path.home() / ".chimera" / "mcp.json"
        project_mcp_path = Path(workdir) / ".mcp.json"
        merged_config: dict[str, Any] = {"servers": {}}
        for cand in (mcp_config_path, project_mcp_path):
            if not cand.exists():
                continue
            import json as _json
            try:
                data = _json.loads(cand.read_text())
            except Exception as exc:  # noqa: BLE001
                print(f"[mcp] could not parse {cand}: {exc}")
                continue
            servers = data.get("servers") or data.get("mcpServers") or {}
            if isinstance(servers, dict):
                merged_config["servers"].update(servers)
        if merged_config["servers"]:
            from chimera.mcp.tools import MCPToolSource
            _mcp_client, _loaded_tools = MCPToolSource.from_config(merged_config)
            mcp_extra_tools = list(_loaded_tools)
    except Exception as exc:  # noqa: BLE001
        # Non-fatal; user sees nothing if their MCP config is broken,
        # so log it (but don't crash the REPL).
        print(f"[mcp] load failed: {exc}")

    # Auto-discover project context
    # WHY (audit M-19): bare ``except: pass`` previously swallowed every
    # error here, so a malformed ``.chimera.toml`` left the user with no
    # signal at all. Surface the error on stderr; discovery stays
    # best-effort (we still continue with the default system prompt).
    system = _DEFAULT_SYSTEM
    try:
        from chimera.config.loader import ProjectConfig
        project = ProjectConfig.from_directory(workdir)
        if project and project.rules_text:
            system += "\n\n# Project Context\n" + project.rules_text
    except Exception as exc:  # noqa: BLE001
        print(f"[project-config] discovery failed: {exc}", file=sys.stderr)

    # Auto-discover skills
    # WHY (audit M-19): same pattern as above — log instead of swallow so a
    # broken SKILL.md surfaces visibly without crashing the REPL.
    try:
        from chimera.skills.discovery import discover_skills, default_search_paths, format_skills_for_prompt
        skills = discover_skills(default_search_paths(workdir))
        skills_section = format_skills_for_prompt(skills)
        if skills_section:
            system += "\n\n" + skills_section
    except Exception as exc:  # noqa: BLE001
        print(f"[skills] discovery failed: {exc}", file=sys.stderr)

    # --- Wire all pi-mono features ---
    # WHY (audit B-2): the polished MinkStreamHandler (Markdown stream +
    # spinner + collapsed tool blocks + diffs) is opt-in for ``chimera code``
    # via ``CHIMERA_RICH_TUI=1``. ``chimera mink`` flips this on by default
    # via its own wiring. Plain ConsoleStreamHandler stays the default here
    # so existing ``code`` workflows do not regress. Pipes / NO_COLOR /
    # non-TTY stdout still fall back to plain text inside ``build_stream_handler``.
    if os.environ.get("CHIMERA_RICH_TUI", "").strip().lower() in {"1", "true", "yes", "on"}:
        from chimera.cli.render import build_stream_handler as _build_handler

        handler = _build_handler()
    else:
        handler = ConsoleStreamHandler()
    cost_tracker = CostTracker()
    file_tracker = FileTracker()
    queues = MessageQueues()

    # WHY (audit B-4 second half): mirror the mink CLI hook wiring so the
    # interactive REPL also honors ``.claude/settings.json`` hooks. This is
    # additive — when the user ALSO has ``.chimera/settings.json`` hooks
    # (loaded via ``HookLoader`` elsewhere), both fire. When neither is
    # configured, ``hook_emitter`` stays None and nothing changes.
    hook_emitter: Any = None
    try:
        from chimera.mink.cli import _build_hook_emitter
        from chimera.mink.settings import load_mink_settings

        _settings = load_mink_settings(cwd=Path(workdir))
        hook_emitter = _build_hook_emitter(dict(_settings.hooks or {}))
    except Exception as exc:  # noqa: BLE001
        # Non-fatal: malformed settings.json shouldn't kill the REPL.
        print(f"[hooks] settings.json hooks load failed: {exc}")

    max_steps = getattr(args, "max_steps", 50) or 50
    config = LoopConfig(
        handler=handler,
        cost_tracker=cost_tracker,
        file_tracker=file_tracker,
        message_queues=queues,
        hook_emitter=hook_emitter,
    )
    loop = ReAct(max_steps=max_steps, config=config)

    prompt = Prompt.from_string(system)

    # Build tool list: AGENT_TOOLS + AskUserTool with REPL callback
    from chimera.tools.ask_user import AskUserTool

    def _repl_ask_callback(question: str, choices: list[str] | None = None) -> str:
        if choices:
            print(f"\n[Agent asks] {question}")
            for i, c in enumerate(choices, 1):
                print(f"  {i}. {c}")
            return input("Your answer: ").strip()
        else:
            print(f"\n[Agent asks] {question}")
            return input("Your answer: ").strip()

    ask_tool = AskUserTool(callback=_repl_ask_callback)
    tools = list(AGENT_TOOLS) + [ask_tool] + mcp_extra_tools

    # Session with auto-persisting tree + auto-compaction
    from chimera.compaction.summary import SummaryCompaction
    tree = SessionTree(_session_path(workdir))
    compaction = SummaryCompaction(keep_first=2, keep_last=10)
    agent = Agent(provider=provider, tools=tools, loop=loop, prompt=prompt)
    session = Session(agent=agent, env=env, tree=tree, auto_compact=True, compaction=compaction)
    session.provider = provider  # type: ignore[attr-defined]
    session.cost_tracker = cost_tracker  # type: ignore[attr-defined]
    session.tools = tools  # type: ignore[attr-defined]
    session.debug = False  # type: ignore[attr-defined]

    # Model cycling
    models_arg = getattr(args, "models", "")
    model_list = [m.strip() for m in models_arg.split(",") if m.strip()] if models_arg else [provider.model_name]
    session._model_list = model_list  # type: ignore[attr-defined]
    session._model_index = 0  # type: ignore[attr-defined]

    _setup_readline()

    print(f"chimera code v{__version__} | model: {provider.model_name} | /help for commands")
    total_cost = 0.0

    while True:
        # IDLE MODE — readline active
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

        # RUNNING MODE — agent in background thread, poll for steering
        cancel_token = CancellationToken()
        config.cancellation = cancel_token
        agent_result_box: list[Any] = [None]

        def _run_agent(msg: str = user_input) -> None:
            try:
                agent_result_box[0] = drain_steps(session.iter_chat(msg))
            except Exception as exc:
                agent_result_box[0] = exc

        agent_thread = threading.Thread(target=_run_agent, daemon=True)
        agent_thread.start()

        # Poll for steering input while agent runs
        try:
            while agent_thread.is_alive():
                line = _read_steering_input()
                if line:
                    queues.steer(Message.user(line))
                    print("  (steering sent)")
        except KeyboardInterrupt:
            cancel_token.cancel()
            print("\n  (cancelling...)")
            agent_thread.join(timeout=10)

        agent_thread.join(timeout=1)

        # Show result
        result = agent_result_box[0]
        if isinstance(result, Exception):
            print(f"\n  Error: {result}", file=sys.stderr)
        elif result is not None:
            total_cost += result.cost
            print(f"\n  [cost: ${result.cost:.4f} | steps: {result.steps}]")

    print(f"\nTotal cost: ${total_cost:.4f}")
    env.cleanup()
    return 0


async def _run_new_stack(model: str, preset: str, cwd: str) -> None:
    """REPL using the new CodingAgent assembly.

    Note: this is a lean REPL. The rich REPL (default, no --preset) currently
    has more slash commands (sessions, checkpoints, tree, steering). Parity is
    tracked for v0.3.
    """
    from chimera.assembly.coding_agent import CodingAgent
    from chimera.core.loop_events import LoopEventType

    agent = CodingAgent(model=model, preset=preset, project_dir=cwd)
    print(f"Chimera ({preset}) — {model} — {len(agent.tools)} tools")
    print("Type /help for commands, Ctrl+C to exit\n")

    total_cost = 0.0

    def print_help() -> None:
        print("Commands:")
        print("  /help         — show this list")
        print("  /tools        — list available tools")
        print("  /model        — show the active model")
        print("  /cost         — show cumulative cost for this session")
        print("  /clear        — clear the screen")
        print("  /exit, /quit  — leave the REPL")
        print()
        print("For session trees, checkpoints, steering, and /compact use the")
        print("rich REPL (run `chimera code` without --preset).")

    while True:
        try:
            user_input = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue

        # Handle local slash commands that don't need the model
        if user_input == "/exit" or user_input == "/quit":
            print("Bye!")
            break
        if user_input == "/help":
            print_help()
            continue
        if user_input == "/tools":
            for t in agent.tools:
                desc = getattr(t, "description", "") or ""
                print(f"  {t.name}: {desc[:60]}")
            continue
        if user_input == "/model":
            print(f"  Model: {agent.provider.model_name}")
            continue
        if user_input == "/cost":
            print(f"  Cumulative cost: ${total_cost:.4f}")
            continue
        if user_input == "/clear":
            # ANSI clear-screen + move cursor to top-left. Works on most terminals.
            print("\033[2J\033[H", end="", flush=True)
            continue
        if user_input.startswith("/"):
            print(f"Unknown command: {user_input}. Type /help for available commands.")
            continue

        # Run through CodingAgent
        agent.reset_abort()
        try:
            async for event in agent.run(user_input):
                t = event.type
                if t == LoopEventType.assistant:
                    content = getattr(event.data, 'content', str(event.data))
                    if content.strip():
                        print(content)
                elif t == LoopEventType.tool_result:
                    tc, result = event.data if isinstance(event.data, tuple) else (None, event.data)
                    tool_name = getattr(tc, 'name', '?') if tc else '?'
                    output = getattr(result, 'output', str(result))
                    success = getattr(result, 'success', True)
                    if output.strip():
                        marker = "+" if success else "!"
                        # Truncate long output
                        if len(output) > 2000:
                            output = output[:1000] + f"\n... [{len(output)-2000} chars truncated] ...\n" + output[-1000:]
                        print(f"[{marker} {tool_name}] {output}")
                elif t == LoopEventType.assistant_chunk:
                    # Streaming text
                    print(str(event.data), end="", flush=True)
                elif t == LoopEventType.error:
                    print(f"[ERROR] {event.data}")
                elif t == LoopEventType.result:
                    # Turn complete — show cost if available
                    cost = getattr(event.data, 'cost_usd', 0)
                    turns = getattr(event.data, 'turn_count', 0)
                    if cost > 0:
                        total_cost += cost
                        print(f"  ({turns} turns, ${cost:.4f})")
                elif t == LoopEventType.system:
                    # Slash command output from InputHandler
                    if event.data:
                        print(event.data)
        except KeyboardInterrupt:
            agent.abort()
            print("\n[Interrupted]")
        except Exception as e:
            print(f"[ERROR] {e}")


def _run_rpc_mode(args: Any) -> int:
    """Run in headless RPC mode (stdin/stdout JSON lines)."""
    from chimera.rpc.handler import RpcHandler
    from chimera.rpc.server import RpcServer

    workdir = os.path.abspath(getattr(args, "workdir", None) or os.getcwd())
    provider = create_provider(model=getattr(args, "model", None))
    env = LocalEnvironment(workdir=workdir)
    env.setup()

    file_tracker = FileTracker()
    queues = MessageQueues()
    config = LoopConfig(
        file_tracker=file_tracker,
        message_queues=queues,
    )
    max_steps = getattr(args, "max_steps", 50) or 50
    loop = ReAct(max_steps=max_steps, config=config)
    prompt = Prompt.from_string(_DEFAULT_SYSTEM)
    tools = list(AGENT_TOOLS)

    tree = SessionTree(_session_path(workdir))
    agent = Agent(provider=provider, tools=tools, loop=loop, prompt=prompt)
    session = Session(agent=agent, env=env, tree=tree)

    server = RpcServer(session)
    handler = RpcHandler(server)
    server.set_handlers(handler.handlers)
    server.run()

    env.cleanup()
    return 0


def _run_json_mode(args: Any) -> int:
    """Run in JSON output mode — single prompt from stdin, JSON events to stdout."""
    import json as json_mod

    workdir = os.path.abspath(getattr(args, "workdir", None) or os.getcwd())
    provider = create_provider(model=getattr(args, "model", None))
    env = LocalEnvironment(workdir=workdir)
    env.setup()

    config = LoopConfig()
    max_steps = getattr(args, "max_steps", 50) or 50
    loop = ReAct(max_steps=max_steps, config=config)
    prompt = Prompt.from_string(_DEFAULT_SYSTEM)
    tools = list(AGENT_TOOLS)
    agent = Agent(provider=provider, tools=tools, loop=loop, prompt=prompt)
    session = Session(agent=agent, env=env)

    # Read prompt from stdin
    user_input = sys.stdin.read().strip()
    if not user_input:
        return 1

    result = drain_steps(session.iter_chat(user_input))
    json_mod.dump({
        "output": result.output,
        "steps": result.steps,
        "cost": result.cost,
        "success": result.success,
    }, sys.stdout)
    sys.stdout.write("\n")

    env.cleanup()
    return 0
