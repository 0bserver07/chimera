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


def cmd_max_cost(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """Raise / lower / clear the per-turn cost cap mid-session.

    Usage:

    * ``/max-cost`` — print the current cap (or ``unset``).
    * ``/max-cost <usd>`` — set the cap to *<usd>* dollars. Accepts ``0``
      (refuse every priced turn) and floats (``/max-cost 0.05``).
    * ``/max-cost off`` / ``/max-cost none`` — clear the cap entirely;
      the REPL stops gating turns until a new cap is set.

    The cap is stashed on ``session._max_cost`` so the same value is
    visible to the per-turn gate in :func:`run_code` and to the W12-9
    test suite that monkeypatches ``estimate_cost``.
    """
    text = args.strip()
    current = getattr(session, "_max_cost", None)

    if not text:
        if current is None:
            out("Per-turn cost cap: unset (no gating).")
        else:
            out(f"Per-turn cost cap: ${float(current):.4f}")
        out("Set with /max-cost <usd>, clear with /max-cost off.")
        return

    lowered = text.lower()
    if lowered in {"off", "none", "unset", "clear"}:
        session._max_cost = None
        out("Per-turn cost cap cleared. REPL will not gate turns until a "
            "new cap is set.")
        return

    try:
        new_cap = float(text)
    except ValueError:
        out(f"Invalid value: {text!r}. Use /max-cost <usd> or "
            "/max-cost off.")
        return

    if new_cap < 0:
        out(f"Invalid value: {new_cap}. Cap must be >= 0.")
        return

    session._max_cost = new_cap
    out(f"Per-turn cost cap set to ${new_cap:.4f}.")


def cmd_force_send(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """Bypass the per-turn cost cap for the very next user turn.

    Sets a one-shot flag on the session so the next non-slash input is
    submitted regardless of ``_max_cost``. The flag is consumed by the
    gate in :func:`run_code` after a single turn, so the safety net is
    automatically re-armed.

    Usage: ``/force-send`` — toggle the one-shot bypass on. Repeating
    the command before submitting a turn is a no-op (the flag is
    already set). After the next turn fires, gating resumes.
    """
    session._force_send_once = True
    out("Force-send armed. The next turn will bypass the --max-cost cap.")


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
    "max-cost": cmd_max_cost,
    "force-send": cmd_force_send,
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
    """Readline completer for slash commands.

    Pulls the live name list via :func:`_command_names` so commands
    registered after readline was set up — notably the user-defined
    custom commands loaded by :mod:`chimera.otter.repl` from
    ``.opencode/command/*.md`` — surface in tab completion. Falling
    back to the static :data:`_COMMAND_NAMES` keeps this safe if the
    shared registry import ever fails.
    """
    try:
        candidates = _command_names()
    except Exception:  # noqa: BLE001 -- never crash the readline thread
        candidates = _COMMAND_NAMES
    matches = [c for c in candidates if c.startswith(text)]
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


def _gate_turn_by_cost(
    session: Any,
    user_input: str,
    *,
    err: Any = sys.stderr,
) -> bool:
    """Return ``True`` when *user_input* may be sent given the cost cap.

    W12-9 — per-turn cost gating for the REPL. Mirrors the one-shot
    ``-p`` gate in ``chimera.otter.cli._maybe_apply_cost_gate``: estimate
    via :func:`chimera.cli.cost_estimator.estimate_cost`, refuse when the
    estimate exceeds ``session._max_cost``, and surface a friendly
    message that points at ``/max-cost`` and ``/force-send`` for the
    user's next move.

    Args:
        session: Active REPL session. Reads ``provider.model_name``,
            ``_max_cost`` and ``_force_send_once`` (one-shot bypass).
        user_input: The prompt the user just typed at the ``>`` prompt.
        err: Stream to write the refusal message to. ``sys.stderr`` in
            production; tests redirect to ``io.StringIO``.

    Returns:
        ``True`` to let the caller submit the turn, ``False`` to refuse
        and skip back to the readline prompt. The one-shot bypass
        (``_force_send_once``) is consumed before this function returns
        so the cap re-arms automatically.
    """
    cap = getattr(session, "_max_cost", None)
    if cap is None:
        return True
    if getattr(session, "_force_send_once", False):
        # Consume the one-shot flag exactly once so the next turn is
        # gated again. Doing this *before* the estimate keeps the
        # bypass cheap when the user has armed it intentionally.
        session._force_send_once = False
        return True

    # Lazy import: cost_estimator only loads when gating is active.
    from chimera.cli.cost_estimator import (
        ModelNotPriced,
        estimate_cost,
    )

    provider = getattr(session, "provider", None)
    model = getattr(provider, "model_name", None)
    if not model:
        # No model => no estimate. Fail closed: refuse the turn so the
        # user knows the budget guard isn't actually wired.
        print(
            "Refusing turn: no provider model attached; cannot estimate "
            "cost. Clear the cap with /max-cost off if you really want "
            "to proceed.",
            file=err,
        )
        return False

    try:
        est = estimate_cost(model, user_input)
    except ModelNotPriced:
        print(
            f"Refusing turn: model {model!r} has no PRICING entry; cost "
            "estimation is unavailable while --max-cost is set. Type "
            "/max-cost off to disable gating, or /force-send to send "
            "this one turn anyway.",
            file=err,
        )
        return False

    if est.total_usd > float(cap):
        print(
            f"Refusing turn: estimated ${est.total_usd:.4f} exceeds "
            f"--max-cost ${float(cap):.4f}. Type /max-cost <usd> to "
            "raise the cap or send anyway with /force-send",
            file=err,
        )
        return False

    return True


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
    # Load creds/model for `chimera code` WITHOUT polluting the shell, so other
    # coding agents keep their own ANTHROPIC_* config. Priority:
    # shell env > project .env > ~/.config/chimera/env. load_dotenv never
    # overrides a variable that is already set.
    from chimera.config.dotenv import load_dotenv

    _env_dir = os.path.abspath(getattr(args, "workdir", None) or os.getcwd())
    _loaded = load_dotenv(os.path.join(_env_dir, ".env"))
    _loaded += load_dotenv(
        os.path.join(os.path.expanduser("~"), ".config", "chimera", "env"),
    )
    if _loaded:
        print(f"· loaded {len(_loaded)} var(s) (chimera config / .env)", file=sys.stderr)

    mode = getattr(args, "mode", "interactive")
    if mode == "rpc":
        return _run_rpc_mode(args)
    if mode == "json":
        return _run_json_mode(args)

    # New CodingAgent stack — activated by --preset OR by default for bare
    # ``chimera code`` (no --preset, no --legacy-react). Wave 10 G3 flips the
    # bare-REPL default from the legacy ReAct stack to the CodingAgent stack
    # (preset="coding_agent"). Per-CLI shims that still rely on the rich
    # legacy REPL (mink/otter/ferret/badger/shrew/stoat) opt back in by
    # setting ``legacy_react=True`` on their shimmed namespace.
    #
    # Resolution order:
    #   1. ``--preset NAME``     → CodingAgent(preset=NAME)
    #   2. ``--legacy-react``    → legacy ReAct stack (this function below)
    #   3. ``_post_session_init``→ legacy stack (rich-REPL hook marker;
    #                              keeps otter/shrew snapshot wiring green)
    #   4. otherwise (default)   → CodingAgent(preset="coding_agent")
    preset = getattr(args, "preset", None)
    legacy_react = bool(getattr(args, "legacy_react", False))
    has_post_init = callable(getattr(args, "_post_session_init", None))
    use_new_stack = preset is not None or (
        not legacy_react and not has_post_init
    )

    if use_new_stack:
        import asyncio
        # When the caller passed --preset NAME, honor it. Otherwise the bare
        # REPL defaults to "coding_agent" — the canonical, most feature-
        # complete preset (claude_code is a deprecated alias of this one).
        effective_preset = preset or "coding_agent"
        model = getattr(args, "model", None) or os.environ.get(
            "ANTHROPIC_MODEL", "claude-sonnet-4-20250514",
        )
        cwd = os.path.abspath(getattr(args, "workdir", None) or os.getcwd())

        # max_turns: 0/negative -> unlimited (None); absent -> preset default.
        _raw_max_turns = getattr(args, "max_turns", None)
        agent_kwargs: dict[str, Any] = {}
        if _raw_max_turns is not None:
            agent_kwargs["max_turns"] = None if _raw_max_turns <= 0 else _raw_max_turns

        # Multiplexer: list saved cohorts (no TTY needed) and exit.
        if getattr(args, "list_cohorts", False):
            from chimera.tui.multiplex import print_saved_cohorts

            print_saved_cohorts()
            return 0

        # Full-screen Textual TUI (opt-in) — reuses the resolved model/cwd/env.
        # EVERY --tui launch is the multiplexer (issue #172; the old
        # single-agent app is a deprecated shim):
        #   --models a,b,…  → N lanes racing one task, isolated from each
        #                     other (worktree/copy); a single --models entry
        #                     still gets a full lane, defaulting to inplace.
        #   bare --tui      → the daily driver: a ONE-LANE multiplexer
        #                     (inplace isolation, targeted routing,
        #                     single-lane chrome). The model string reaches
        #                     the driver verbatim — never lane-spec parsed.
        if getattr(args, "tui", False):
            # Resume a saved cohort (Phase 3.2): reopen its lanes and continue.
            resume_id = getattr(args, "resume", None)
            if resume_id:
                from chimera.tui.multiplex import resume_multiplexer

                resume_multiplexer(
                    resume_id,
                    isolation=getattr(args, "isolation", None),
                    lane_cap=getattr(args, "lane_cap", None),
                    export=getattr(args, "export", None),
                    **agent_kwargs,
                )
                return 0

            raw_models = getattr(args, "models", "") or ""
            models = [m.strip() for m in raw_models.split(",") if m.strip()]
            if models:
                from chimera.tui.multiplex import default_isolation, run_multiplexer

                run_multiplexer(
                    models=models,
                    project_dir=cwd,
                    preset=effective_preset,
                    task=getattr(args, "print_mode", None),
                    isolation=default_isolation(len(models), getattr(args, "isolation", None)),
                    lane_cap=getattr(args, "lane_cap", None),
                    export=getattr(args, "export", None),
                    **agent_kwargs,
                )
                return 0

            from chimera.tui.multiplex import run_single_agent

            run_single_agent(
                model=model, project_dir=cwd, preset=effective_preset,
                task=getattr(args, "print_mode", None),
                export=getattr(args, "export", None),
                **agent_kwargs,
            )
            return 0

        # Non-interactive -p mode
        print_task = getattr(args, "print_mode", None)
        if print_task:
            from chimera.assembly.coding_agent import CodingAgent
            from chimera.core.loop_events import LoopEventType

            async def _print_run() -> None:
                agent = CodingAgent(
                    model=model, preset=effective_preset, project_dir=cwd,
                    **agent_kwargs,
                )
                saw_chunk = False
                async for event in agent.run(print_task):
                    if event.type == LoopEventType.assistant_chunk:
                        saw_chunk = True
                        print(str(event.data), end="", flush=True)
                    elif event.type == LoopEventType.assistant and not saw_chunk:
                        content = getattr(event.data, 'content', str(event.data))
                        if content.strip():
                            print(content)
                print()

            asyncio.run(_print_run())
            return 0

        asyncio.run(
            _run_new_stack(
                model=model, preset=effective_preset, cwd=cwd,
                agent_kwargs=agent_kwargs,
            ),
        )
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
        from chimera.skills.discovery import discover_all_skills, format_skills_for_prompt
        # discover_all_skills also folds in other harnesses' skill dirs when
        # the opt-in foreign scan is enabled (config / CHIMERA_SKILLS_FOREIGN);
        # default off, so this is unchanged for users who never enable it.
        skills = discover_all_skills(workdir)
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

    # W12-9: per-turn cost gating. ``--max-cost`` (otter, mink) is plumbed
    # through to the REPL via ``args.max_cost``. The cap lives on the
    # session so ``/max-cost`` can raise it mid-session and ``/force-send``
    # can bypass it for one turn without leaking state across turns.
    initial_max_cost = getattr(args, "max_cost", None)
    session._max_cost = (  # type: ignore[attr-defined]
        float(initial_max_cost) if initial_max_cost is not None else None
    )
    session._force_send_once = False  # type: ignore[attr-defined]

    # Optional post-session-init hook. Used by the otter REPL to wire its
    # per-turn snapshot stack (``chimera.otter.repl.install_snapshot_hooks``)
    # without forking the shared interactive loop. ``None`` (the default)
    # leaves behavior identical to before this hook was introduced.
    post_session_init = getattr(args, "_post_session_init", None)
    if callable(post_session_init):
        try:
            post_session_init(session, env)
        except Exception as exc:  # noqa: BLE001 - never crash the REPL
            print(f"[post_session_init] hook failed: {exc}", file=sys.stderr)

    # Surface plugin-contributed UI commands into the shared slash-command
    # registry so a third-party plugin's `/command` works at the prompt without
    # editing core. No-op when no plugin registered a UICommand.
    try:
        from chimera.plugins.ui import install_into_repl

        _installed = install_into_repl()
        if _installed:
            print("plugin commands: " + ", ".join(f"/{n}" for n in _installed))
    except Exception as exc:  # noqa: BLE001 - never crash the REPL
        print(f"[plugin-ui] command install failed: {exc}", file=sys.stderr)

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

        # W12-9: per-turn cost gate. Refuse the turn here (before the
        # agent thread spins up) when the estimate exceeds the active
        # ``--max-cost`` cap. ``/force-send`` opts out for one turn.
        if not _gate_turn_by_cost(session, user_input):
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


async def _run_new_stack(
    model: str, preset: str, cwd: str, agent_kwargs: dict[str, Any] | None = None,
) -> None:
    """REPL using the new CodingAgent assembly.

    Note: this is a lean REPL. The rich REPL (default, no --preset) currently
    has more slash commands (sessions, checkpoints, tree, steering). Parity is
    tracked for v0.3.
    """
    from chimera.assembly.driver import AgentDriver, render_event
    from chimera.core.loop_events import LoopEventType

    driver = AgentDriver(
        model=model, preset=preset, project_dir=cwd, interactive=True,
        **(agent_kwargs or {}),
    )
    _ctx = driver.context_window
    _ctx_str = f"{_ctx:,}" if _ctx else "?"
    print(f"Chimera ({preset}) — {driver.model} — {len(driver.tools)} tools — {_ctx_str} ctx")
    print("Type /help for commands, Ctrl+C to interrupt a turn, /exit to quit\n")

    def print_help() -> None:
        print("Commands:")
        print("  /help          — show this list")
        print("  /tools         — list available tools")
        print("  /model         — model and context window")
        print("  /cost          — cumulative cost this session")
        print("  /history       — messages currently in context")
        print("  /clear         — forget the conversation (fresh context)")
        print("  /cls           — clear the screen")
        print("  /exit, /quit   — leave the REPL")

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
            print(f"Total cost: ${driver.total_cost:.4f}. Bye!")
            break
        if user_input == "/help":
            print_help()
            continue
        if user_input == "/tools":
            for t in driver.tools:
                desc = getattr(t, "description", "") or ""
                print(f"  {t.name}: {desc[:60]}")
            continue
        if user_input == "/model":
            print(f"  {driver.model}  ({_ctx_str} ctx)")
            continue
        if user_input == "/cost":
            print(f"  Cumulative cost: ${driver.total_cost:.4f}")
            continue
        if user_input == "/history":
            print(f"  {len(driver.history)} messages in context")
            continue
        if user_input == "/clear":
            driver.clear()
            print("  (conversation cleared — next message starts fresh)")
            continue
        if user_input == "/cls":
            # ANSI clear-screen + move cursor to top-left. Works on most terminals.
            print("\033[2J\033[H", end="", flush=True)
            continue
        if user_input.startswith("/"):
            print(f"Unknown command: {user_input}. Type /help for available commands.")
            continue

        # Run a turn through the driver. render_event() handles tool calls,
        # results, errors, and compaction; assistant text streams via chunks
        # (with a non-streaming fallback) so nothing prints twice.
        saw_chunk = False
        try:
            async for event in driver.send(user_input):
                t = event.type
                if t == LoopEventType.assistant_chunk:
                    saw_chunk = True
                    print(str(event.data), end="", flush=True)
                elif t == LoopEventType.assistant:
                    if not saw_chunk:
                        content = getattr(event.data, "content", "") or ""
                        if content.strip():
                            print(content)
                elif t == LoopEventType.result:
                    r = event.data
                    cost = getattr(r, "cost_usd", 0) or 0
                    steps = getattr(r, "turn_count", 0)
                    print(f"\n  · {steps} steps · ${cost:.4f} turn · ${driver.total_cost:.4f} total")
                else:
                    line = render_event(event)
                    if line is not None:
                        print(line)
        except KeyboardInterrupt:
            driver.cancel()
            print("\n[interrupted]")
        except Exception as e:
            print(f"\n[error] {e}")


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
