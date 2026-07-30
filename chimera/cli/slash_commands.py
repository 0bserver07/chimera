"""Shared slash-command registry for the Chimera REPL.

This module hosts the canonical command registry used by ``chimera code``
(and, in M1, ``chimera mink``). Handler functions defined in
:mod:`chimera.cli.code` are re-registered here for back-compat; the 11 new
M1 commands are defined inline.

Public API:
    - :func:`register` — add a command at runtime
    - :func:`dispatch` — parse ``/cmd args`` and route to a handler
    - :func:`list_commands` — enumerate ``(name, help_text)`` pairs
    - :data:`COMMAND_NAMES` — sorted list of ``/name`` strings (for tab completion)

Every new command must degrade gracefully: when an underlying Chimera
subsystem is unavailable, the handler must print
``not available: <reason>`` instead of raising.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable
from chimera.config.paths import chimera_home, project_state_dir, store_path

# NOTE: Handlers defined in :mod:`chimera.cli.code` (cmd_help, cmd_model, ...)
# are imported lazily inside :func:`_build_default_registry` to avoid a
# circular import: ``code.py`` lazily imports this module from inside
# ``_dispatch_command`` / ``_command_names``. Resolving ``code.cmd_*`` at
# module load here would force ``code.py`` to finish executing before
# ``slash_commands`` finishes, which fails when ``slash_commands`` is the
# first of the two to be imported. (/cost and /compact are overridden
# below by M4-D — callers needing the legacy stubs can import them from
# :mod:`chimera.cli.code` directly.)

PrintFn = Callable[[str], None]
CommandHandler = Callable[[Any, Any, str, PrintFn], None]

# -- Registry --

_REGISTRY: dict[str, tuple[CommandHandler, str]] = {}

# Tab-completion friendly view, kept in sync with the registry by every
# call to :func:`register`. Defined here (before :func:`register`) so the
# refresh-on-register path has a list to mutate during the initial
# :func:`_build_default_registry` walk; the canonical contents are
# populated below once the default commands land.
COMMAND_NAMES: list[str] = []


def register(name: str, handler: CommandHandler, help_text: str = "") -> None:
    """Register a slash command handler.

    Args:
        name: Command name without the leading slash (e.g. ``"status"``).
        handler: Callable matching :data:`CommandHandler`.
        help_text: One-line description shown by ``/help``.
    """
    _REGISTRY[name] = (handler, help_text)
    # Keep the tab-completion view in sync. ``COMMAND_NAMES`` is the
    # canonical source the REPL completer pulls from; mutating it in
    # place (rather than rebinding the module attribute) means callers
    # who imported the list directly still see the fresh entries.
    _refresh_command_names()


def _refresh_command_names() -> None:
    """Resync :data:`COMMAND_NAMES` with the live :data:`_REGISTRY`.

    Mutates ``COMMAND_NAMES`` in place so prior ``from ... import
    COMMAND_NAMES`` callers (or test fixtures holding a reference to
    the list object) observe the change. Safe to call repeatedly; a
    no-op when the registry has not changed.
    """
    fresh = sorted(f"/{name}" for name in _REGISTRY)
    COMMAND_NAMES[:] = fresh


def refresh_command_names() -> list[str]:
    """Public refresh hook for tab-completion consumers.

    Forces :data:`COMMAND_NAMES` to be recomputed from the live
    registry and returns the resulting list. Called by the otter
    REPL after :func:`chimera.otter.slash.register_custom_commands`
    so user-defined commands surface in ``<TAB>`` completion.

    Returns:
        The refreshed sorted list of ``/name`` strings.
    """
    _refresh_command_names()
    return list(COMMAND_NAMES)


def list_commands() -> list[tuple[str, str]]:
    """Return all registered commands as ``(name, help_text)`` tuples, sorted."""
    return sorted((name, help_text) for name, (_, help_text) in _REGISTRY.items())


def dispatch(line: str, session: Any, env: Any | None = None, out: PrintFn = print) -> bool:
    """Dispatch a single REPL line.

    Args:
        line: Raw input line. Non-slash lines are ignored (returns ``False``).
        session: The active :class:`chimera.sessions.session.Session`.
        env: Optional :class:`chimera.env.base.Environment`.
        out: Print function; defaults to :func:`print`.

    Returns:
        ``True`` if a command was matched and dispatched (even on error);
        ``False`` if ``line`` did not start with ``/`` or the command was unknown.
    """
    if not line.startswith("/"):
        return False
    parts = line[1:].split(maxsplit=1)
    cmd_name = parts[0] if parts else ""
    args = parts[1] if len(parts) > 1 else ""

    entry = _REGISTRY.get(cmd_name)
    if entry is None:
        out(f"Unknown command: /{cmd_name}. Type /help for available commands.")
        return False

    handler, _ = entry
    handler(session, env, args, out)
    return True


# -- /help (overrides the legacy cmd_help so it can render help text) --

def cmd_help(_session: Any, _env: Any, _args: str, out: PrintFn) -> None:
    """List all registered slash commands with help text."""
    out("Available commands:")
    for name, help_text in list_commands():
        if help_text:
            out(f"  /{name:<14} {help_text}")
        else:
            out(f"  /{name}")


# -- 11 new M1 commands --

def cmd_status(session: Any, env: Any, _args: str, out: PrintFn) -> None:
    """Print a one-screen status summary (model, mode, tools, cwd, session, cost)."""
    provider = getattr(session, "provider", None)
    model = getattr(provider, "model_name", "unknown")
    tools = getattr(session, "tools", []) or []
    cwd = getattr(env, "workdir", None) or os.getcwd()
    session_id = getattr(session, "id", None) or getattr(session, "session_id", "n/a")
    yolo = getattr(session, "_yolo_mode", False)
    mode = "yolo" if yolo else "interactive"

    out("Status:")
    out(f"  model:      {model}")
    out(f"  mode:       {mode}")
    out(f"  tools:      {len(tools)}")
    out(f"  cwd:        {cwd}")
    out(f"  session:    {session_id}")

    tracker = getattr(session, "cost_tracker", None)
    if tracker is not None:
        total = getattr(tracker, "total", 0.0)
        out(f"  cost (USD): ${total:.4f}")

    ctx = getattr(session, "context", None)
    if ctx is not None and hasattr(ctx, "to_messages"):
        try:
            msgs = ctx.to_messages()
            chars = sum(len(m.content) for m in msgs)
            out(f"  messages:   {len(msgs)} (~{chars // 4} tokens)")
        except Exception:
            pass


def cmd_doctor(_session: Any, env: Any, _args: str, out: PrintFn) -> None:
    """Run environment health checks (Ollama, settings.json, MCP servers)."""
    # Try the dedicated module first.
    try:
        from chimera.cli import doctor as _doctor  # type: ignore[attr-defined]
        if hasattr(_doctor, "main"):
            _doctor.main()
            return
    except ImportError:
        pass

    out("doctor: running inline checks")

    # Ollama reachability
    ollama_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    try:
        import urllib.request
        with urllib.request.urlopen(f"{ollama_url}/api/tags", timeout=2) as resp:
            ok = resp.status == 200
        out(f"  ollama:        {'ok' if ok else 'fail'} ({ollama_url})")
    except Exception as exc:
        out(f"  ollama:        not available: {exc}")

    # .claude/settings.json validity
    workdir = getattr(env, "workdir", None) or os.getcwd()
    settings_path = Path(workdir) / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            json.loads(settings_path.read_text())
            out(f"  settings.json: ok ({settings_path})")
        except json.JSONDecodeError as exc:
            out(f"  settings.json: invalid JSON: {exc}")
    else:
        out(f"  settings.json: not present ({settings_path})")

    # WHY (audit M-7): users follow CC's `.mcp.json` convention rather
    # than the legacy Chimera path. Check both the project-level
    # `.mcp.json` (CC-style) and the user-level `~/.chimera/mcp.json`
    # (legacy Chimera) so the doctor reflects what the live CLI loads.
    mcp_user = chimera_home() / "mcp.json"
    mcp_project = Path(workdir) / ".mcp.json"
    mcp_project_alt = Path(workdir) / ".claude" / ".mcp.json"
    sources_present: list[tuple[Path, int | str]] = []
    for source in (mcp_project, mcp_project_alt, mcp_user):
        if not source.exists():
            continue
        try:
            cfg = json.loads(source.read_text())
            servers = cfg.get("mcpServers", cfg.get("servers", {}))
            sources_present.append((source, len(servers)))
        except Exception as exc:
            sources_present.append((source, f"parse error: {exc}"))
    if not sources_present:
        out(
            "  mcp servers:   none configured "
            "(checked .mcp.json, .claude/.mcp.json, ~/.chimera/mcp.json)"
        )
    else:
        out("  mcp servers:")
        for source, payload in sources_present:
            if isinstance(payload, int):
                out(f"    {source}: {payload} configured")
            else:
                out(f"    {source}: {payload}")


def _get_permission_checker(session: Any) -> Any | None:
    agent = getattr(session, "agent", None) or getattr(session, "_agent", None)
    if agent is None:
        return None
    loop = getattr(agent, "loop", None)
    config = getattr(loop, "config", None) if loop else None
    if config is None:
        return None
    return getattr(config, "permissions", None) or getattr(config, "permission_checker", None)


def cmd_permissions(session: Any, _env: Any, _args: str, out: PrintFn) -> None:
    """Print active permission ruleset (allow/ask/deny lists, mode)."""
    checker = _get_permission_checker(session)
    if checker is None:
        out("not available: no permission checker on this session")
        return

    ruleset = getattr(checker, "ruleset", None) or checker
    mode = getattr(checker, "mode", None) or getattr(ruleset, "mode", "default")
    out(f"Permissions (mode: {mode}):")

    for kind in ("allow", "ask", "deny"):
        rules = (
            getattr(ruleset, f"{kind}_rules", None)
            or getattr(ruleset, kind, None)
            or []
        )
        try:
            rules = list(rules)
        except TypeError:
            rules = []
        out(f"  {kind} ({len(rules)}):")
        for r in rules[:20]:
            out(f"    - {r}")
        if len(rules) > 20:
            out(f"    ... and {len(rules) - 20} more")


def cmd_hooks(_session: Any, env: Any, _args: str, out: PrintFn) -> None:
    """List registered hooks (event -> matchers -> command)."""
    try:
        from chimera.hooks.loader import HookLoader
    except ImportError as exc:
        out(f"not available: {exc}")
        return

    workdir = getattr(env, "workdir", None) or os.getcwd()
    try:
        from chimera.hooks.events import HookEvent
        loader = HookLoader(project_dir=str(workdir))
    except Exception as exc:
        out(f"not available: {exc}")
        return

    out("Hooks:")
    found = 0
    for event in HookEvent:
        try:
            matchers = loader.load_all(event)
        except Exception:
            continue
        if not matchers:
            continue
        out(f"  {event.value}:")
        for matcher in matchers:
            pattern = getattr(matcher, "matcher", "*")
            for hook in getattr(matcher, "hooks", []) or []:
                cmd = getattr(hook, "command", None) or getattr(hook, "prompt", "")
                out(f"    [{pattern}] -> {cmd}")
                found += 1
    if found == 0:
        out("  (none registered)")


def cmd_mcp(_session: Any, env: Any, _args: str, out: PrintFn) -> None:
    """List active MCP servers and their tools.

    Reads three configs in CC's precedence order (project > project/.claude
    > user), merges the server maps, and prints the combined list. Per the
    audit (M-4), restricting to ``~/.chimera/mcp.json`` ignored the
    documented project-level ``.mcp.json`` that ``25-implementation-plan``
    promised to honor.
    """
    workdir = getattr(env, "workdir", None) or os.getcwd()
    candidates = [
        ("project (.mcp.json)", Path(workdir) / ".mcp.json"),
        ("project (.claude/.mcp.json)", Path(workdir) / ".claude" / ".mcp.json"),
        ("user (~/.chimera/mcp.json)", chimera_home() / "mcp.json"),
    ]
    found_any = False
    merged_servers: dict[str, Any] = {}
    for label, path in candidates:
        if not path.exists():
            continue
        found_any = True
        try:
            cfg = json.loads(path.read_text())
        except Exception as exc:
            out(f"  {label}: parse error: {exc}")
            continue
        scope_servers = cfg.get("mcpServers", cfg.get("servers", {}))
        if not isinstance(scope_servers, dict):
            continue
        out(f"  {label}: {len(scope_servers)} servers")
        # WHY: project scopes win over user (last-update-wins on shared keys),
        # mirroring CC's precedence ladder.
        merged_servers.update(scope_servers)
    if not found_any:
        out("not available: no .mcp.json found in project or ~/.chimera/")
        return

    out(f"MCP servers ({len(merged_servers)} after merge):")
    for name, spec in merged_servers.items():
        cmd = spec.get("command", "?") if isinstance(spec, dict) else "?"
        out(f"  {name}: {cmd}")

    # Best-effort: ask MCPToolSource for the live tool list.
    try:
        from chimera.mcp.tools import MCPToolSource
        # MCPToolSource.from_config returns (client, tools) per current API.
        result = MCPToolSource.from_config({"servers": merged_servers})
        if isinstance(result, tuple) and len(result) == 2:
            tools = list(result[1])
        else:
            tools = list(getattr(result, "tools", None) or [])
        if tools:
            out(f"  tools: {len(tools)}")
            for t in tools[:30]:
                out(f"    - {getattr(t, 'name', t)}")
    except Exception as exc:
        out(f"  (live tool listing not available: {exc})")


def _inject_messages(session: Any, messages: list[Any]) -> int:
    """Replace the live session's context messages with *messages*.

    Args:
        session: Active :class:`chimera.sessions.session.Session` instance.
        messages: Messages to install. The system prompt from the
            existing context is preserved.

    Returns:
        Number of messages injected.
    """
    from chimera.core.context import Context

    ctx = getattr(session, "context", None) or getattr(session, "_context", None)
    system = getattr(ctx, "system", None) if ctx is not None else None
    new_ctx = Context(system=system) if system is not None else Context()
    for msg in messages:
        new_ctx.add(msg)
    session._context = new_ctx  # type: ignore[attr-defined]
    return len(messages)


class _ResumeStubPrompt:
    """Minimal duck-typed prompt for ``Session.resume`` rebuilds.

    ``Session.resume`` only reads ``agent.prompt.render(tools=...)`` while
    seeding the initial Context, which we throw away after extraction.
    """

    def render(self, tools: list[str] | None = None) -> str:
        return ""


class _ResumeStubAgent:
    """Minimal duck-typed Agent used to satisfy ``Session.resume``.

    The reload path only inspects ``agent.prompt`` and ``agent.tools``;
    everything else is grabbed from the persisted log.
    """

    def __init__(self) -> None:
        self.prompt = _ResumeStubPrompt()
        self.tools: list[Any] = []


def _list_resumable_sessions(limit: int = 10) -> list[tuple[str, float, str]]:
    """Return the ``limit`` most-recent resumable session ids.

    Walks both the event-sourced log root (``~/.chimera/eventlog/``)
    and the file-storage root (``~/.chimera/sessions/``) and returns
    a sorted list of ``(session_id, mtime, source)`` tuples — newest
    first. Missing roots produce no entries (no error).

    Args:
        limit: Maximum number of rows to return. Defaults to 10.

    Returns:
        ``[(id, mtime, source), ...]`` where ``source`` is one of
        ``"eventlog"`` or ``"file"``. Ids are deduplicated by name —
        when both backends have the same id, the freshest mtime wins
        and ``eventlog`` is preferred on ties.
    """
    rows: dict[str, tuple[float, str]] = {}

    eventlog_root = store_path("eventlog")
    if eventlog_root.is_dir():
        for child in eventlog_root.iterdir():
            if not child.is_dir():
                continue
            try:
                mtime = child.stat().st_mtime
            except OSError:
                continue
            rows[child.name] = (mtime, "eventlog")

    sessions_root = store_path("sessions")
    if sessions_root.is_dir():
        for child in sessions_root.iterdir():
            if not child.is_file() or child.suffix not in (".json", ".jsonl"):
                continue
            try:
                mtime = child.stat().st_mtime
            except OSError:
                continue
            sid = child.stem
            existing = rows.get(sid)
            # WHY: eventlog wins on ties — a richer replay path.
            if existing is None or mtime > existing[0]:
                rows[sid] = (mtime, "file")

    sortable = [(sid, mtime, source) for sid, (mtime, source) in rows.items()]
    sortable.sort(key=lambda r: r[1], reverse=True)
    return sortable[:limit]


def cmd_resume(session: Any, _env: Any, args: str, out: PrintFn) -> None:
    """Resume a saved session by id, or list the most-recent ones.

    Tries the event-sourced log first (``~/.chimera/eventlog/<id>/``),
    then falls back to :class:`chimera.sessions.storage.file.FileStorage`
    (``~/.chimera/sessions/<id>.json``).

    Per audit M-6, the prior implementation refused to run unless
    ``session.agent`` was wired — but ``Session.resume`` only needs a
    duck-typed object exposing ``prompt`` + ``tools``. We supply a tiny
    stub when the live session has no agent so ``/resume`` is usable from
    a bare REPL too.

    When called with no arg (``/resume``) we print the 10 most-recent
    resumable session ids — a lightweight non-interactive picker. The
    canonical interactive flow remains ``/resume <id>``.

    Args:
        session: Active session whose context will be replaced.
        _env: Unused.
        args: ``<session_id>`` (whitespace stripped). Empty value lists
            recent sessions.
        out: Print function.
    """
    sid = args.strip()
    if not sid:
        rows = _list_resumable_sessions(limit=10)
        if not rows:
            out(
                "No resumable sessions found under ~/.chimera/eventlog/ "
                "or ~/.chimera/sessions/. Pass /resume <id> once you have one."
            )
            return
        out("Recent sessions (newest first) — pass id to /resume <id>:")
        import datetime as _dt

        for i, (rid, mtime, source) in enumerate(rows, 1):
            stamp = _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            out(f"  {i:>2}. [{source:>8}] {rid}  ({stamp})")
        return

    # WHY (audit M-6): ``Session.resume`` only reads ``agent.prompt`` and
    # ``agent.tools`` while seeding the initial Context, so a duck-typed
    # stub is sufficient when the live session has no real agent attached.
    # The cast hides the structural-typing escape hatch from mypy.
    from typing import cast as _cast
    from chimera.core.agent import Agent as _Agent

    raw_agent = (
        getattr(session, "_agent", None)
        or getattr(session, "agent", None)
        or _ResumeStubAgent()
    )
    agent = _cast(_Agent, raw_agent)

    # Path 1: EventSourcedSession (preferred — full event replay).
    eventlog_root = store_path("eventlog")
    eventlog_dir = eventlog_root / sid
    if eventlog_dir.exists():
        try:
            from chimera.sessions.eventlog.session import EventSourcedSession

            restored = EventSourcedSession.resume(
                log_dir=eventlog_root, session_id=sid, agent=agent,
            )
            n = _inject_messages(session, list(restored.messages))
            session._session_id = sid  # type: ignore[attr-defined]
            out(f"resumed {n} messages from session {sid}")
            return
        except Exception as exc:  # noqa: BLE001
            out(f"event-log resume failed: {exc}")
            # fall through to FileStorage path below

    # Path 2: FileStorage (one JSON per session).
    try:
        from chimera.sessions.session import Session
        from chimera.sessions.storage.file import FileStorage

        storage = FileStorage()
        restored_file = Session.resume(session_id=sid, agent=agent, storage=storage)
    except ValueError as exc:
        out(f"session not found: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        out(f"resume failed: {exc}")
        return

    n = _inject_messages(session, list(restored_file.messages))
    session._session_id = sid  # type: ignore[attr-defined]
    out(f"resumed {n} messages from session {sid}")


def cmd_cost(session: Any, _env: Any, _args: str, out: PrintFn) -> None:
    """Show per-model + per-step cost / token breakdown via :class:`CostTracker`.

    Surfaces the ``CostTracker.summary()`` dict (per-model token columns,
    per-step timeline, total). Falls back to a friendly message when no
    tracker is attached to the session.
    """
    tracker = getattr(session, "cost_tracker", None)
    if tracker is None:
        out("No cost tracker active.")
        return

    try:
        summary = tracker.summary()
    except Exception as exc:  # noqa: BLE001
        out(f"not available: {exc}")
        return

    out("Cost summary:")
    out(f"  total: ${summary.get('total_cost', 0.0):.4f} "
        f"({summary.get('total_calls', 0)} calls)")
    out(f"  tokens: in={summary.get('total_input_tokens', 0)} "
        f"out={summary.get('total_output_tokens', 0)} "
        f"cache_r={summary.get('total_cache_read_tokens', 0)} "
        f"cache_w={summary.get('total_cache_write_tokens', 0)} "
        f"reason={summary.get('total_reasoning_tokens', 0)}")
    cache_hit = summary.get("cache_hit_rate", 0.0)
    if cache_hit:
        out(f"  cache hit rate: {cache_hit * 100:.1f}%")

    by_model = summary.get("by_model", {}) or {}
    if by_model:
        out("Per-model:")
        for model, m in sorted(by_model.items()):
            out(
                f"  {model}: ${m.get('cost', 0.0):.4f} "
                f"in={m.get('input_tokens', 0)} "
                f"out={m.get('output_tokens', 0)} "
                f"cache_r={m.get('cache_read_tokens', 0)} "
                f"reason={m.get('reasoning_tokens', 0)} "
                f"calls={m.get('calls', 0)}"
            )

    steps = getattr(tracker, "steps", None) or []
    if steps:
        out(f"Per-step ({len(steps)}):")
        for step in steps[-10:]:
            out(
                f"  step {step.step_index}: ${step.total_cost:.4f} "
                f"in={step.total_input_tokens} out={step.total_output_tokens} "
                f"reason={step.total_reasoning_tokens} "
                f"({step.duration:.2f}s)"
            )

    budget_remaining = summary.get("budget_remaining")
    if budget_remaining is not None:
        out(f"Remaining budget: ${budget_remaining:.4f}")


def cmd_compact(session: Any, _env: Any, _args: str, out: PrintFn) -> None:
    """Force a HARD :class:`ThresholdCompaction` on the live context.

    Builds a one-shot ``ThresholdCompaction`` with ``hard_threshold=0.0``
    so any non-empty context triggers an emergency reset (system prompt
    + last N messages). Prints ``tokens_before / tokens_after / Δ%`` and
    a one-line preview of the synthetic summary message.
    """
    ctx = getattr(session, "context", None) or getattr(session, "_context", None)
    if ctx is None:
        out("Compaction not available: no context.")
        return

    messages = list(getattr(ctx, "messages", []) or [])
    if not messages:
        out("Compaction not available: context is empty.")
        return

    # Token estimate matches CompactionView's heuristic (~4 chars/token).
    def _tok(msgs: list[Any]) -> int:
        return sum(len(str(getattr(m, "content", ""))) // 4 for m in msgs)

    tokens_before = _tok(messages)

    try:
        from chimera.compaction.base import CompactionView
        from chimera.compaction.summary import SummaryCompaction
        from chimera.compaction.thresholds import ThresholdCompaction

        keep_last = 5
        compactor = ThresholdCompaction(
            strategy=SummaryCompaction(keep_first=1, keep_last=keep_last),
            soft_threshold=0.0,
            hard_threshold=0.0,
            max_context_tokens=max(tokens_before, 1),
            keep_last=keep_last,
        )
        compacted_view = compactor.compact(CompactionView(messages))
    except Exception as exc:  # noqa: BLE001
        out(f"not available: {exc}")
        return

    new_messages = list(compacted_view.messages)
    tokens_after = _tok(new_messages)
    # Context exposes messages as a public list attribute; assign in
    # place so any cached reference (e.g. session.context.messages) sees
    # the new contents.
    ctx.messages = new_messages

    delta_pct = (
        ((tokens_before - tokens_after) / tokens_before * 100.0)
        if tokens_before else 0.0
    )
    out(
        f"compacted: {len(messages)} -> {len(new_messages)} messages, "
        f"{tokens_before} -> {tokens_after} tokens ({delta_pct:.1f}% reduced)"
    )

    # Preview the first synthesized summary message inserted.
    for m in new_messages:
        content = str(getattr(m, "content", "") or "")
        if "[Previous context was compressed" in content or "[Compacted" in content:
            preview = content.replace("\n", " ")[:120]
            out(f"  summary: {preview}")
            break


def cmd_sandbox(session: Any, _env: Any, _args: str, out: PrintFn) -> None:
    """Toggle sandbox mode if available."""
    try:
        from chimera.permissions import sandbox as _sandbox  # type: ignore[attr-defined]
    except ImportError as exc:
        out(f"not available: {exc}")
        return

    toggle = getattr(_sandbox, "toggle", None)
    if toggle is None:
        out("not available: sandbox toggle not implemented")
        return
    try:
        new_state = toggle(session)
        out(f"sandbox: {'on' if new_state else 'off'}")
    except Exception as exc:
        out(f"not available: {exc}")


def cmd_subagent(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """Spawn a registered subagent and print its result.

    Per audit M-2, the prior implementation degraded to ``not available``
    because (a) the built-in preset registry was instantiated but its
    contents were never advertised to the user, and (b) ``AgentConfig.build()``
    requires a provider — calling ``builder()`` with no args raised. The
    fix surfaces ``/subagent`` (no args) as a list of available presets,
    threads the live session's provider into ``build()``, and falls back
    to the file-based ``AgentLoader`` for ``.claude/agents/*.md`` defs.
    """
    parts = args.strip().split(maxsplit=1)
    if not parts or not parts[0]:
        # WHY (audit M-2): list available presets when invoked bare so
        # users discover what they can spawn.
        try:
            from chimera.agents.loader import create_default_registry
            preset_names = sorted(create_default_registry().list())
        except Exception as exc:
            out(f"not available: {exc}")
            return
        out("Usage: /subagent <name> <prompt>")
        if preset_names:
            out("Built-in presets: " + ", ".join(preset_names))
        return
    if len(parts) < 2:
        out("Usage: /subagent <name> <prompt>")
        return
    name, prompt = parts[0], parts[1]

    try:
        from chimera.agents.loader import AgentLoader, create_default_registry
    except ImportError as exc:
        out(f"not available: {exc}")
        return

    try:
        registry = create_default_registry()
    except Exception as exc:
        out(f"not available: {exc}")
        return

    provider = getattr(session, "provider", None)

    agent_def = registry.get(name) if name in registry.list() else None
    if agent_def is None:
        # Fall through to the file-based AgentLoader (handles
        # `.claude/agents/*.md` user/project agents).
        try:
            workdir = getattr(env, "workdir", None) or os.getcwd()
            loader = AgentLoader(project_root=workdir)
            file_def = loader.get(name)
        except Exception as exc:
            out(f"not available: {exc}")
            return
        if file_def is None:
            out(f"not available: subagent '{name}' not registered")
            return
        if provider is None:
            out("not available: live session has no provider to bind the subagent")
            return
        try:
            from chimera.agents.loader import AgentFactory
            from chimera.core.tool_group import AGENT_TOOLS
            tool_registry = {t.name: t for t in AGENT_TOOLS}
            factory = AgentFactory(provider=provider, tool_registry=tool_registry)
            sub_agent = factory.create(file_def)
        except Exception as exc:
            out(f"subagent error: {exc}")
            return
    else:
        if provider is None:
            out("not available: live session has no provider to bind the subagent")
            return
        try:
            sub_agent = agent_def.build(provider=provider)
        except Exception as exc:
            out(f"subagent error: {exc}")
            return

    out(f"Spawning subagent '{name}'...")
    try:
        result = sub_agent.run(prompt, env=env) if hasattr(sub_agent, "run") else None
        if result is None:
            out("(subagent returned no result)")
            return
        # AgentResult-like with .output, or raw string.
        output = getattr(result, "output", None)
        if output is None:
            output = str(result)
        out(str(output)[:2000])
    except Exception as exc:
        out(f"subagent error: {exc}")


def cmd_plugin(session: Any, _env: Any, args: str, out: PrintFn) -> None:
    """Manage plugins: list / install / enable / disable / discover.

    Per audit M-3 / M-16, the prior implementation always reported
    ``No plugins loaded`` because it instantiated a fresh
    :class:`PluginManager` for every call. We now stash one manager per
    session under ``session._plugin_manager`` so list/install reflect
    state across slash invocations, and ``enable`` / ``disable`` are
    aliased onto :meth:`PluginManager.load` / :meth:`PluginManager.unload`
    so the user-visible verbs do something instead of printing
    ``not implemented``.
    """
    parts = args.strip().split(maxsplit=1)
    sub = parts[0] if parts else "list"
    target = parts[1] if len(parts) > 1 else ""

    try:
        from chimera.plugins.manager import PluginManager
    except ImportError as exc:
        out(f"not available: {exc}")
        return

    # WHY: cache the manager on the session so successive list/install
    # calls see the same _plugins map instead of starting blank each time.
    mgr = getattr(session, "_plugin_manager", None)
    if mgr is None:
        try:
            mgr = PluginManager()
        except Exception as exc:
            out(f"not available: {exc}")
            return
        try:
            setattr(session, "_plugin_manager", mgr)
        except (AttributeError, TypeError):
            # Frozen / opaque session — keep the local manager only.
            pass

    if sub == "list":
        try:
            plugins = mgr.plugins
            if not plugins:
                out("No plugins loaded. Use /plugin discover to enumerate available entry points.")
                return
            out("Loaded plugins:")
            for name in sorted(plugins):
                out(f"  {name}")
        except Exception as exc:
            out(f"not available: {exc}")
    elif sub == "discover":
        try:
            available = mgr.discover()
        except Exception as exc:
            out(f"not available: {exc}")
            return
        if not available:
            out("No plugin entry points registered under chimera.plugins.")
            return
        out(f"Available plugin entry points ({len(available)}):")
        for name in sorted(available):
            out(f"  {name}")
    elif sub in ("install", "enable"):
        if not target:
            out(f"Usage: /plugin {sub} <name>")
            return
        try:
            mgr.load(target)
            out(f"{sub}d: {target}")
        except Exception as exc:
            out(f"{sub} failed: {exc}")
    elif sub in ("uninstall", "disable"):
        if not target:
            out(f"Usage: /plugin {sub} <name>")
            return
        try:
            mgr.unload(target)
            out(f"{sub}d: {target}")
        except Exception as exc:
            out(f"{sub} failed: {exc}")
    else:
        out(
            f"Unknown plugin command: {sub}. "
            "Try: list, discover, install, enable, disable, uninstall"
        )


def cmd_diff(session: Any, env: Any, args: str, out: PrintFn) -> None:
    """Show ``git diff`` for files modified during this session.

    Wraps ``git diff HEAD`` (and ``git diff --stat HEAD`` for the
    summary view) executed from the agent's working directory. When the
    live session has a :class:`~chimera.core.file_tracker.FileTracker`
    attached, we narrow the diff to *just* the files that tracker has
    seen modified — that gives a tighter signal than the full
    working-tree diff in long-running REPL sessions.

    Usage:
        ``/diff`` — full working-tree diff vs ``HEAD``.
        ``/diff stat`` — ``git diff --stat HEAD`` summary.
        ``/diff <path>`` — diff a specific path vs ``HEAD``.

    Trademark hygiene: the slash mirrors the cross-CLI standard. No
    competitor brand names are emitted in help text or output.

    Args:
        session: Active session (read for an attached ``file_tracker``).
        env: Environment whose ``workdir`` anchors the git invocation.
        args: Optional argument: ``stat`` for summary, or a path.
        out: Print function.
    """
    workdir = getattr(env, "workdir", None) or os.getcwd()
    if shutil.which("git") is None:
        out("not available: git not on PATH")
        return

    raw = (args or "").strip()
    base_cmd = ["git", "diff", "HEAD"]

    # WHY: when no explicit path is given, prefer the FileTracker scope
    # so a long REPL run shows just the files the agent touched.
    paths: list[str] = []
    if raw == "stat":
        base_cmd = ["git", "diff", "--stat", "HEAD"]
    elif raw:
        paths = [raw]
    else:
        tracker = getattr(session, "file_tracker", None)
        if tracker is not None:
            for attr in ("modified_files", "modified", "files_modified"):
                seen = getattr(tracker, attr, None)
                if seen:
                    try:
                        paths = [str(p) for p in seen]
                    except TypeError:
                        paths = []
                    break

    cmd = list(base_cmd)
    if paths:
        cmd.append("--")
        cmd.extend(paths)

    try:
        result = subprocess.run(
            cmd,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        out(f"not available: git diff failed: {exc}")
        return

    # WHY: a non-zero rc with ``not a git repository`` is the most
    # common failure mode; surface it clearly instead of dumping stderr
    # raw at the user.
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if "not a git repository" in stderr.lower():
            out(f"not available: {workdir} is not a git repository")
            return
        out(f"git diff failed (rc={result.returncode}): {stderr or 'no stderr'}")
        return

    body = result.stdout or ""
    if not body.strip():
        if paths:
            out(f"No diff vs HEAD for: {', '.join(paths)}")
        else:
            out("No diff vs HEAD (working tree is clean).")
        return
    if paths and raw != "stat":
        out(f"# /diff scope: {len(paths)} file(s) vs HEAD")
    out(body.rstrip("\n"))


def cmd_review(session: Any, env: Any, _args: str, out: PrintFn) -> None:
    """Run the review orchestrator against the current git diff.

    Per audit M-5, the prior implementation collapsed reviewer + author
    onto a single ``session.agent``, which breaks the multi-agent
    contract (the reviewer/author distinction is the whole point of the
    orchestrator). We now build a fresh reviewer Agent from the
    ``review`` preset when ``session.provider`` is available, and only
    fall back to single-agent mode if preset construction fails.
    """
    try:
        from chimera.review.orchestrator import ReviewOrchestrator
    except ImportError as exc:
        out(f"not available: {exc}")
        return

    workdir = getattr(env, "workdir", None) or os.getcwd()
    if shutil.which("git") is None:
        out("not available: git not on PATH")
        return
    try:
        diff = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except Exception as exc:
        out(f"not available: git diff failed: {exc}")
        return

    if not diff.strip():
        out("No staged or unstaged changes (git diff HEAD is empty).")
        return

    provider = getattr(session, "provider", None)
    if provider is None:
        out("not available: no provider on this session")
        return

    try:
        orchestrator = ReviewOrchestrator()
    except Exception as exc:
        out(f"not available: {exc}")
        return

    author = getattr(session, "agent", None) or getattr(session, "_agent", None)
    if author is None:
        out("not available: no author agent attached to session")
        return

    # WHY (audit M-5): build a distinct reviewer agent from the ``review``
    # preset so the orchestrator gets the two roles it expects. Falls back
    # to single-agent mode (with a warning) if preset construction fails.
    reviewer = author
    used_preset = False
    try:
        from chimera.agents.loader import create_default_registry
        reg = create_default_registry()
        review_def = reg.get("review") if "review" in reg.list() else None
        if review_def is not None:
            reviewer = review_def.build(provider=provider)
            used_preset = True
    except Exception as exc:
        out(f"  (review preset unavailable, reusing author agent: {exc})")
    if not used_preset:
        out("  (warning: reviewer == author; multi-agent review degraded to single-agent)")

    out(f"Running review on {len(diff)} bytes of diff...")
    try:
        approved = orchestrator.run(diff, reviewer=reviewer, author=author, env=env)
        out(f"review approved: {approved} (rounds: {orchestrator.current_round})")
        for round_obj in orchestrator.rounds:
            for comment in getattr(round_obj.feedback, "comments", []) or []:
                summary = getattr(comment, "summary", str(comment))
                out(f"  - {summary}")
    except Exception as exc:
        out(f"review error: {exc}")


def cmd_resync(session: Any, env: Any, _args: str, out: PrintFn) -> None:
    """Hot-swap plugins / skills / agent definitions from disk, mid-session.

    ``/resync`` re-discovers the resource catalogs (plugin source, SKILL.md
    trees, agent definition files) and rebinds them into the live session:
    edit a plugin or a skill on disk, ``/resync``, and the next turn uses
    the new behavior. Reports added / removed / refreshed / failed per
    resource kind, refuses while a turn is running, and states honestly
    whether the system prompt could be rebuilt for this session.

    Routes by session shape: a session driving the assembled stack (an
    agent exposing ``resync_resources``) uses the assembly seam; the classic
    REPL session goes through
    :func:`chimera.assembly.resync.resync_session`.
    """
    try:
        # Assembled stack: the agent owns the full rebind seam.
        agent = getattr(session, "agent", None) or getattr(session, "_agent", None)
        resync = getattr(agent, "resync_resources", None)
        if callable(resync):
            report = resync()
        else:
            from chimera.assembly.resync import resync_session

            report = resync_session(session, env)
    except Exception as exc:  # noqa: BLE001 - degrade, never crash the REPL
        out(f"not available: {exc}")
        return
    for line in report.lines():
        out(line)


def cmd_config(_session: Any, env: Any, _args: str, out: PrintFn) -> None:
    """Print effective merged settings.

    Per audit M-8, the prior implementation called ``json.dumps(settings)``
    on a :class:`MinkSettings` dataclass and silently fell through to
    ``repr`` because dataclasses are not JSON-serializable directly. We
    now serialize via :func:`dataclasses.asdict` so the rendered output
    is the proper JSON the user expects.
    """
    import dataclasses as _dc

    try:
        from chimera.mink import settings as mink_settings  # type: ignore[attr-defined]
        if hasattr(mink_settings, "load_mink_settings"):
            settings = mink_settings.load_mink_settings()
            out("Effective settings (mink):")
            try:
                if _dc.is_dataclass(settings) and not isinstance(settings, type):
                    payload = _dc.asdict(settings)
                    out(json.dumps(payload, indent=2, default=str))
                else:
                    out(json.dumps(settings, indent=2, default=str))
            except Exception as exc:
                # Last-resort surface so the user knows serialization
                # failed instead of silently seeing repr.
                out(f"  (could not serialize: {exc})")
                out(repr(settings))
            return
    except ImportError:
        pass

    workdir = getattr(env, "workdir", None) or os.getcwd()
    candidates = [
        project_state_dir(workdir) / "settings.json",
        Path(workdir) / ".claude" / "settings.json",
        chimera_home() / "settings.json",
    ]
    found = False
    for path in candidates:
        if not path.exists():
            continue
        found = True
        try:
            data = json.loads(path.read_text())
            out(f"# {path}")
            out(json.dumps(data, indent=2))
        except Exception as exc:
            out(f"# {path}")
            out(f"  (parse error: {exc})")
    if not found:
        out("not available: no settings.json found in .chimera/, .claude/, or ~/.chimera/")


# -- Build the registry --

def _build_default_registry() -> None:
    """Populate :data:`_REGISTRY` with the 19 ported commands + 11 new ones."""
    # Lazy import to break the chimera.cli.code <-> chimera.cli.slash_commands
    # circular import (see module-level note above).
    from chimera.cli.code import (
        cmd_agent,
        cmd_audit,
        cmd_branch,
        cmd_checkpoint,
        cmd_clear,
        cmd_context,
        cmd_debug,
        cmd_exit,
        cmd_force_send,
        cmd_history,
        cmd_init,
        cmd_max_cost,
        cmd_model,
        cmd_session,
        cmd_switch,
        cmd_tools,
        cmd_tree,
        cmd_yolo,
    )

    # 19 ported commands (cmd_help is overridden above)
    register("help", cmd_help, "show this list")
    register("model", cmd_model, "show or cycle the active model")
    register("cost", cmd_cost, "show cumulative cost (per-model + per-step)")
    register(
        "max-cost",
        cmd_max_cost,
        "show / set / clear the per-turn cost cap (e.g. /max-cost 0.05)",
    )
    register(
        "force-send",
        cmd_force_send,
        "bypass --max-cost for the next turn",
    )
    register("clear", cmd_clear, "clear context")
    register("history", cmd_history, "show recent messages")
    register("tools", cmd_tools, "list available tools")
    register("context", cmd_context, "show context size")
    register("debug", cmd_debug, "toggle debug mode")
    register("session", cmd_session, "session save/list/fork")
    register("compact", cmd_compact, "force a HARD threshold compaction now")
    register("audit", cmd_audit, "show audit log")
    register("checkpoint", cmd_checkpoint, "checkpoint save/list/restore/undo")
    register("agent", cmd_agent, "list agent presets")
    register("exit", cmd_exit, "leave the REPL")
    register("quit", cmd_exit, "leave the REPL")
    register("init", cmd_init, "summarise the project")
    register("yolo", cmd_yolo, "toggle auto-approve mode")
    register("tree", cmd_tree, "show session tree")
    register("branch", cmd_branch, "branch from an entry")
    register("switch", cmd_switch, "switch to a leaf")
    # 11 new M1 commands
    register("status", cmd_status, "one-screen status summary")
    register("doctor", cmd_doctor, "environment health checks")
    register("permissions", cmd_permissions, "show active permission ruleset")
    register("hooks", cmd_hooks, "list registered hooks")
    register("mcp", cmd_mcp, "list MCP servers and tools")
    register(
        "resume",
        cmd_resume,
        "resume a saved session by id (no arg = list recent)",
    )
    register(
        "diff",
        cmd_diff,
        "git diff vs HEAD (no arg = files modified this session)",
    )
    register("sandbox", cmd_sandbox, "toggle sandbox mode")
    register("subagent", cmd_subagent, "spawn a registered subagent")
    register("plugin", cmd_plugin, "list/install/enable/disable plugins")
    register("resync", cmd_resync, "hot-swap plugins/skills/agents from disk")
    register("review", cmd_review, "review current git diff")
    register("config", cmd_config, "print effective merged settings")


_build_default_registry()
# At this point :data:`COMMAND_NAMES` was already populated incrementally
# by every ``register(...)`` call above. The explicit refresh keeps this
# resilient to refactors where someone reaches into :data:`_REGISTRY`
# directly without going through :func:`register`.
_refresh_command_names()
