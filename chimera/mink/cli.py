"""``chimera mink`` — Mink, a Chimera coding agent on Ollama Kimi K2.6.

Wires the existing ``chimera code`` REPL machinery to an
``OllamaProvider`` defaulting to ``kimi-k2.6:cloud`` with graceful
fallback. See ``research/mink/25-implementation-plan.md`` §3 M1 for
the milestone scope and exit criteria.

The subcommand has two modes:

* Interactive (default): drops into the same ``run_code`` REPL the
  ``chimera code`` subcommand uses — slash-commands, steering, tree
  branching, and Ctrl-C cancellation are all reused unchanged.
* One-shot ``-p/--print PROMPT``: runs a single turn against the
  configured model, prints the assistant output, and exits. The
  ``--output-format`` flag selects ``text``, ``json`` (single object
  on exit), or ``stream-json`` (one JSON line per ``LoopEvent``).
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

# WHY: only stdlib + chimera at import time so `from chimera.cli import cc`
# stays cheap; httpx is pulled in lazily inside ``_build_provider``.

_DEFAULT_MODEL = "kimi-k2.6:cloud"
_DEFAULT_FALLBACK = "qwen3:32b"


# WHY (audit M-17): Session.resume / EventSourcedSession.resume only need
# ``agent.prompt.render()`` + ``agent.tools`` to seed Context, then we throw
# the resumed session away after extracting messages. This single Protocol-
# conforming shim replaces the four nested _StubAgent / _StubPrompt classes
# the file used to define + cast through ``Agent``.


class _ResumeAgentPromptShim:
    """Render-only Prompt stand-in used by ``_apply_launch_resume``.

    Matches the structural ``_PromptLike`` Protocol declared in
    :mod:`chimera.sessions.session`: a single ``render`` method whose
    return value is immediately overwritten by replayed Context.
    """

    def render(self, tools: list[str] | None = None) -> str:
        return ""


class _ResumeAgentShim:
    """Minimal :class:`SessionResumeAgent` impl for the mink resume flow.

    ``Session.__init__`` (called by both ``Session.resume`` and
    ``EventSourcedSession.resume``) reads ``self.prompt.render(tools=[...])``
    and iterates ``self.tools`` to derive tool names for that render call.
    Empty ``tools`` is fine — the resumed Context is overlaid with saved
    state immediately afterwards.
    """

    def __init__(self) -> None:
        # WHY: annotate as ``Any`` so mypy uses structural matching against
        # the SessionResumeAgent Protocol (which expects ``_PromptLike``)
        # rather than rejecting the concrete subtype name.
        self.prompt: Any = _ResumeAgentPromptShim()
        self.tools: list[Any] = []


def _resolve_version() -> str:
    """Resolve the chimera package version for ``--version`` output.

    Prefers ``chimera.__version__`` (PEP 562 lazy attribute) and falls back
    to :func:`importlib.metadata.version` for the ``chimera-run`` distribution
    so editable installs without an explicit ``__version__`` still print
    something useful.

    Returns:
        A version string, or ``"unknown"`` when neither source is reachable.
    """
    try:
        from chimera import __version__ as _v

        return str(_v)
    except Exception:  # noqa: BLE001
        try:
            from importlib.metadata import version as _meta_version

            return str(_meta_version("chimera-run"))
        except Exception:  # noqa: BLE001
            return "unknown"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register ``chimera mink`` flags on ``parser``.

    Kept separate from ``build_parser`` so tests and embedders can
    attach the same surface to their own parsers without re-running
    subcommand registration.
    """
    # WHY (audit H-1): expose `chimera mink --version` so packaging scripts
    # and users can confirm what they installed without launching the REPL.
    parser.add_argument(
        "--version",
        action="version",
        version=f"chimera mink {_resolve_version()}",
    )
    # WHY (audit H-4): per-tool-call timeout. When set, each tool dispatch
    # is wrapped in `asyncio.wait_for(...)`; on timeout the loop emits a
    # synthetic error result and continues rather than crashing.
    parser.add_argument(
        "--tool-timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Per-tool-call timeout in seconds. When exceeded, the tool "
        "returns an error result (the agent can react) instead of crashing "
        "the run. Default: no timeout.",
    )
    # WHY: env-var precedence is --model > $CHIMERA_MINK_MODEL > _DEFAULT_MODEL.
    # Lets CI / shell sessions pin a tag once while keeping ad-hoc --model
    # overrides cheap. Mirrors the existing $CHIMERA_MINK_FALLBACK pattern.
    parser.add_argument(
        "--model",
        default=os.environ.get("CHIMERA_MINK_MODEL") or _DEFAULT_MODEL,
        help=f"Ollama model tag (default: $CHIMERA_MINK_MODEL or {_DEFAULT_MODEL}). "
        f"Falls back to $CHIMERA_MINK_FALLBACK (default {_DEFAULT_FALLBACK}) "
        "if the primary model is unreachable.",
    )
    parser.add_argument(
        "--permission-mode",
        choices=["default", "acceptEdits", "bypassPermissions", "plan"],
        default="default",
        help="Permission mode (ecosystem parity). 'default' asks for risky ops; "
        "'acceptEdits' auto-approves edits; 'bypassPermissions' skips all "
        "prompts; 'plan' is read-only planning mode.",
    )
    parser.add_argument(
        "--allowed-tools",
        default="",
        help="Comma-separated tool names to allow (ecosystem parity). Empty = all.",
    )
    parser.add_argument(
        "--resume",
        default=None,
        help="Resume a session by id (matches ~/.chimera/sessions/<id>.jsonl).",
    )
    parser.add_argument(
        "--agent",
        default=None,
        help="Agent preset name to load via the agent registry.",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory (default: current directory).",
    )
    # WHY (issue #127): when set, route file/bash tools through a remote
    # SSHEnvironment instead of LocalEnvironment. Format mirrors git/scp:
    #   ssh://user@host[:port][/abs/path]
    # Authentication piggybacks on ~/.ssh/config + ssh-agent (no password
    # prompts in the scaffold). Live testing requires CHIMERA_SSH_TEST_HOST.
    parser.add_argument(
        "--remote",
        default=None,
        metavar="SSH_URL",
        help="Run tools on a remote host over SSH. Format: "
        "ssh://user@host[:port][/path]. Uses ~/.ssh/config + agent for "
        "auth. Default: run locally.",
    )
    parser.add_argument(
        "-p",
        "--print",
        dest="print_mode",
        default=None,
        help="One-shot: run a single turn with PROMPT, print, exit.",
    )
    parser.add_argument(
        "--output-format",
        choices=["text", "json", "stream-json"],
        default="text",
        help="One-shot output format. 'stream-json' prints one JSON line per "
        "LoopEvent; 'json' prints a single result object on exit.",
    )
    # WHY: max-steps is exposed by the underlying `chimera code` REPL — we
    # surface a parallel default so users get the same ceiling here.
    parser.add_argument(
        "--max-steps",
        type=int,
        default=50,
        help="Maximum agent steps per turn (default: 50).",
    )
    # WHY: persistence defaults ON for one-shot --print runs so users can
    # `--resume` them later. --no-save lets pipelines opt out (e.g. when
    # the prompt itself is sensitive and shouldn't hit disk).
    parser.add_argument(
        "--no-save",
        action="store_true",
        default=False,
        help="Do not persist the one-shot run to ~/.chimera/eventlog/. "
        "Default behavior saves the full message + tool history.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Override the auto-generated run id for the persisted "
        "eventlog directory. Useful for reproducible test fixtures.",
    )
    # WHY (audit B-2 / B-7 / B-8 + H-2): MinkStreamHandler is the new default
    # on a TTY but users piping to a file/grep want the plain handler.
    # ``--no-rich``/``--no-color`` are synonyms: explicit opt-outs that force
    # the plain ConsoleStreamHandler. Pipes auto-disable rich via ``isatty()``
    # detection, and the ``NO_COLOR`` env var is honored as well.
    parser.add_argument(
        "--no-rich",
        action="store_true",
        default=False,
        help="Force the plain ConsoleStreamHandler even when stdout is a TTY. "
        "Default behavior auto-selects: rich on TTY, plain when piped.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        help="Synonym for --no-rich. Also honored implicitly when the "
        "$NO_COLOR environment variable is set.",
    )
    # WHY (audit H-3): expose persisted runs through a small positional
    # subcommand surface so users can list and inspect them without
    # ``cat``-ing JSON by hand. Positional dispatch keeps this orthogonal
    # to the top-level argparse subparser slot already taken by
    # ``chimera <command>`` — we reuse ``args.runs_command`` etc. inside
    # ``run()``. ``runs`` is the only positional we accept; everything
    # else is parsed via the existing flag set above.
    parser.add_argument(
        "runs_command",
        nargs="?",
        default=None,
        choices=[None, "runs", "agents"],
        metavar="SUBCOMMAND",
        help="Optional: 'runs' to inspect persisted ~/.chimera/eventlog runs, "
        "or 'agents' to list/show available agent presets.",
    )
    parser.add_argument(
        "runs_action",
        nargs="?",
        default=None,
        choices=[None, "list", "show", "share", "cost"],
        metavar="ACTION",
        help="With 'runs' or 'agents': 'list' (table), 'show <name|id>' (detail), "
        "'share <run-id>' (export tarball; runs only), "
        "or 'cost' (aggregate cost across persisted runs; runs only).",
    )
    parser.add_argument(
        "runs_target",
        nargs="?",
        default=None,
        metavar="TARGET",
        help="Run id consumed by 'runs show', or agent name consumed by 'agents show'.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        default=False,
        help="With 'runs show <id>': dump every event as readable markdown.",
    )
    # WHY (audit H-3, refinement): filter + display flags for the runs
    # subcommand. Naming uses ``runs_*`` to avoid clashing with top-level
    # mink flags like ``--model`` (which the runs filter shadows by going
    # through ``--runs-model``).
    parser.add_argument(
        "--limit",
        dest="runs_limit",
        type=int,
        default=20,
        help="With 'runs list': cap the rows shown (default 20; <=0 = unlimited).",
    )
    parser.add_argument(
        "--runs-model",
        dest="runs_filter_model",
        default=None,
        help="With 'runs list': only show runs whose model name matches this.",
    )
    parser.add_argument(
        "--success-only",
        dest="runs_success_only",
        action="store_true",
        default=False,
        help="With 'runs list': only show runs where success=true.",
    )
    parser.add_argument(
        "--failed-only",
        dest="runs_failed_only",
        action="store_true",
        default=False,
        help="With 'runs list': only show runs where success=false.",
    )
    parser.add_argument(
        "--events",
        dest="runs_show_events",
        action="store_true",
        default=True,
        help="With 'runs show': include the event transcript (default).",
    )
    parser.add_argument(
        "--no-events",
        dest="runs_show_events",
        action="store_false",
        help="With 'runs show': suppress the event transcript.",
    )
    # WHY (issue #129): ``--sink`` selects the share backend for
    # ``runs share <id>``. Default is ``file`` so the command works
    # offline; ``gist`` requires ``gh auth``; ``base64`` returns a
    # data URI suitable for inline pastes.
    parser.add_argument(
        "--sink",
        dest="runs_share_sink",
        choices=["gist", "file", "base64"],
        default="file",
        help="With 'runs share': export backend (default: file).",
    )
    # WHY (M4): ``runs cost`` flags. ``--since`` / ``--format`` are unique
    # to ``cost`` so they get their own dest names; ``--runs-model``
    # already exists for ``runs list`` and is reused here as the model
    # filter so users only learn one flag.
    parser.add_argument(
        "--since",
        dest="runs_cost_since",
        default=None,
        help="With 'runs cost': window to aggregate over. Accepts shorthand "
        "(e.g. '7d', '24h', '30m') or an ISO-8601 date.",
    )
    parser.add_argument(
        "--format",
        dest="runs_cost_format",
        choices=["text", "json", "csv"],
        default="text",
        help="With 'runs cost': output format (default: text).",
    )


# ---------------------------------------------------------------------------
# Remote (SSH) environment helpers — issue #127
# ---------------------------------------------------------------------------


def _parse_remote_url(url: str) -> dict[str, Any]:
    """Parse ``ssh://user@host[:port][/path]`` into kwargs for SSHEnvironment.

    Args:
        url: A URL string starting with ``ssh://``. Bare ``user@host`` (no
            scheme) is also accepted as a convenience and treated as
            ``ssh://user@host``.

    Returns:
        Dict with keys ``host`` (always ``user@host`` when a username was
        supplied, else ``host``), ``port`` (int, default 22), and
        ``workdir`` (str, default ``"."``). Suitable for ``**``-splat
        into :class:`SSHEnvironment`.

    Raises:
        ValueError: When the URL has no host component.
    """
    from urllib.parse import urlparse

    raw = url if "://" in url else f"ssh://{url}"
    parsed = urlparse(raw)
    if not parsed.hostname:
        raise ValueError(f"--remote URL missing hostname: {url!r}")
    host = (
        f"{parsed.username}@{parsed.hostname}"
        if parsed.username
        else parsed.hostname
    )
    workdir = parsed.path.lstrip("/") or "."
    # ``/abs/path`` should stay absolute on the remote side; ``urlparse``
    # strips the leading slash above, so re-add it when the original had one.
    if parsed.path.startswith("//") or (
        parsed.path.startswith("/") and parsed.path != "/"
    ):
        workdir = parsed.path
    return {
        "host": host,
        "port": parsed.port or 22,
        "workdir": workdir,
    }


def _build_environment(args: argparse.Namespace, cwd: str) -> Any:
    """Instantiate :class:`SSHEnvironment` or :class:`LocalEnvironment`.

    Centralized so ``_run_print_mode`` and any future entry points pick
    the same backend from the same flag set. ``setup()`` is called by
    the caller, not here, so cleanup ordering stays explicit.

    Args:
        args: Parsed CLI namespace; reads ``args.remote``.
        cwd: Local working directory (used when ``--remote`` is unset).

    Returns:
        A live :class:`~chimera.env.base.Environment` ready for
        ``setup()``.
    """
    remote = getattr(args, "remote", None)
    if remote:
        from chimera.env.ssh import SSHEnvironment

        kwargs = _parse_remote_url(remote)
        return SSHEnvironment(**kwargs)
    from chimera.env.local import LocalEnvironment

    return LocalEnvironment(workdir=cwd)


# ---------------------------------------------------------------------------
# Provider construction with fallback
# ---------------------------------------------------------------------------


def _build_provider(model: str) -> Any:
    """Construct an ``OllamaProvider`` with M0-style fallback.

    Mirrors ``examples/mink_walking_skeleton.py``: probe ``/api/tags`` once,
    and if the primary model is unreachable degrade to
    ``$CHIMERA_MINK_FALLBACK`` (default ``qwen3:32b``) with a 131k context.

    Args:
        model: Primary Ollama model tag.

    Returns:
        A live ``OllamaProvider`` instance.
    """
    from chimera.providers.ollama import OllamaProvider

    host = os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
    # WHY: also accept the old CHIMERA_CC_FALLBACK so existing setups
    # don't break overnight; warn once when it's the only source.
    fallback = os.environ.get("CHIMERA_MINK_FALLBACK")
    if fallback is None:
        legacy = os.environ.get("CHIMERA_CC_FALLBACK")
        if legacy is not None:
            print(
                "[deprecated] CHIMERA_CC_FALLBACK is deprecated; "
                "use CHIMERA_MINK_FALLBACK instead.",
                file=sys.stderr,
            )
            fallback = legacy
        else:
            fallback = _DEFAULT_FALLBACK

    # Cloud Kimi advertises 262k; local fallbacks max near 131k.
    primary_ctx = 262_144 if model.startswith("kimi") else 131_072

    try:
        import httpx  # type: ignore[import-not-found]

        httpx.get(f"{host.rstrip('/')}/api/tags", timeout=3).raise_for_status()
        return OllamaProvider(model=model, base_url=host, context_length=primary_ctx)
    except Exception as exc:  # noqa: BLE001 — match walking-skeleton breadth
        print(
            f"[warn] {model} unavailable ({exc}); falling back to {fallback}",
            file=sys.stderr,
        )
        return OllamaProvider(
            model=fallback, base_url=host, context_length=131_072
        )


# ---------------------------------------------------------------------------
# Session resume / cwd / permission-mode plumbing
# ---------------------------------------------------------------------------


def _resume_path(session_id: str) -> Path | None:
    """Resolve a ``--resume`` argument to an existing session file."""
    candidate = Path.home() / ".chimera" / "sessions" / f"{session_id}.jsonl"
    return candidate if candidate.exists() else None


def _permission_mode_to_enum(value: str) -> Any:
    """Map the CLI flag spelling to the internal ``PermissionMode``."""
    from chimera.permissions.modes import PermissionMode

    return {
        "default": PermissionMode.DEFAULT,
        "acceptEdits": PermissionMode.ACCEPT_EDITS,
        "bypassPermissions": PermissionMode.BYPASS,
        "plan": PermissionMode.PLAN,
    }[value]


def _policy_for_mode(value: str) -> Any:
    # WHY: LoopConfig.permissions wants the simple PermissionPolicy ABC
    # (sync .evaluate). The heavy multi-source PermissionChecker is interactive-
    # REPL territory; for one-shot --print we map the CLI mode flag to the
    # appropriate preset so tool dispatch actually works.
    from chimera.permissions.presets import (
        AutoApprove,
        Interactive,
        ReadOnly,
    )

    if value == "bypassPermissions":
        return AutoApprove()
    if value == "plan":
        return ReadOnly()
    if value == "acceptEdits":
        return AutoApprove()
    return Interactive()


# ---------------------------------------------------------------------------
# Agent preset resolution (audit H-6)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _ResolvedAgentSpec:
    """Concrete agent definition resolved from ``--agent <name>``.

    Attributes:
        name: Resolved agent name (typically the value of ``--agent``).
        system_prompt: Markdown body to use as the system prompt.
        tools: Tool name list (entries unknown to ``AGENT_TOOLS`` are
            silently dropped at the call site with a warning).
        model: Optional model override. ``None`` means "no agent-supplied
            model"; the CLI ``--model`` flag wins regardless.
        source: Where the definition was loaded from
            (``project`` / ``user`` / ``builtin``). Used by ``__repr__``.
    """

    name: str
    system_prompt: str
    tools: list[str]
    model: str | None
    source: str


def _resolve_agent_spec(name: str, cwd: Path) -> _ResolvedAgentSpec | None:
    """Resolve ``--agent <name>`` to a concrete :class:`_ResolvedAgentSpec`.

    Searches in this priority order (first match wins):

    1. ``<cwd>/.claude/agents/<name>.md`` (project scope, ecosystem parity)
    2. ``~/.claude/agents/<name>.md`` (user scope, ecosystem parity)
    3. :class:`AgentLoader` (which itself walks ``.chimera/agents/``,
       ``~/.chimera/agents/``, and the built-in registry).
    4. Built-in :class:`AgentRegistry` presets (``build``, ``explore``,
       ``general``, ``plan``, ``review``).

    Args:
        name: The agent name to resolve.
        cwd: Working directory used to anchor project-scoped lookups.

    Returns:
        A :class:`_ResolvedAgentSpec`, or ``None`` if nothing matched.
    """
    project_md = cwd / ".claude" / "agents" / f"{name}.md"
    user_md = Path.home() / ".claude" / "agents" / f"{name}.md"

    for path, source in ((project_md, "project"), (user_md, "user")):
        if path.is_file():
            try:
                from chimera.agents.loader import FileAgentDef

                fdef = FileAgentDef.from_file(path, source=source)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[mink] warning: failed to parse {path} ({exc}); "
                    "falling back to next agent source",
                    file=sys.stderr,
                )
                continue
            return _ResolvedAgentSpec(
                name=fdef.name,
                system_prompt=fdef.system_prompt,
                tools=list(fdef.tools),
                model=fdef.model,
                source=source,
            )

    # Fall back to AgentLoader (walks .chimera/agents/ etc + built-ins).
    try:
        from chimera.agents.loader import AgentLoader

        loader = AgentLoader(project_root=str(cwd))
        loaded = loader.get(name)
    except Exception:  # noqa: BLE001
        loaded = None
    if loaded is not None:
        return _ResolvedAgentSpec(
            name=loaded.name,
            system_prompt=loaded.system_prompt,
            tools=list(loaded.tools),
            model=loaded.model,
            source=loaded.source or "loader",
        )

    # Final fallback: built-in preset registry (build/explore/general/...).
    try:
        from chimera.agents.loader import create_default_registry

        registry = create_default_registry()
        cfg = registry.get(name)
    except Exception:  # noqa: BLE001
        cfg = None
    if cfg is not None:
        return _ResolvedAgentSpec(
            name=cfg.name,
            system_prompt=cfg.system_prompt,
            tools=list(cfg.tools),
            model=cfg.model,
            source="builtin",
        )

    return None


# ---------------------------------------------------------------------------
# Hook executor wiring (B-4 second half)
# ---------------------------------------------------------------------------


def _build_hook_emitter(settings_hooks: dict[str, list[dict[str, Any]]]) -> Any | None:
    """Translate ``settings.hooks`` into a :class:`HookEmitter` ready for LoopConfig.

    Reads the parsed ``hooks`` block from :func:`load_mink_settings` (whose
    keys are CC event names like ``"PreToolUse"`` and whose values are lists
    of hook spec dicts) and converts each one into the right Chimera hook
    type (``CommandHook`` / ``PromptHook``) wrapped in a ``HookMatcher``.
    The returned :class:`HookEmitter` is wired with a fresh
    :class:`HookExecutor` so :class:`LoopConfig.hook_emitter` callers fire
    the hooks before/after every tool call.

    Args:
        settings_hooks: The ``hooks`` dict from ``MinkSettings``. Empty or
            missing returns ``None``, leaving current behavior unchanged.

    Returns:
        A configured ``HookEmitter`` or ``None`` if no hooks are declared.
    """
    if not settings_hooks:
        return None

    from chimera.hooks.emitter import HookEmitter
    from chimera.hooks.executor import HookExecutor
    from chimera.hooks.hook_types import CommandHook, HookMatcher, PromptHook

    matchers: list[Any] = []
    for _event_name, specs in settings_hooks.items():
        if not isinstance(specs, list):
            continue
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            # CC ships two equivalent hook shapes:
            #  - flat:    {"type":"command","command":"echo X","matcher":"Bash"}
            #  - nested:  {"matcher":"Bash","hooks":[{"type":"command",
            #             "command":"echo X"}]}
            # We accept both so users can copy a stock CC settings.json.
            inner_specs: list[dict[str, Any]] = []
            matcher = spec.get("matcher")
            if isinstance(spec.get("hooks"), list):
                inner_specs = [s for s in spec["hooks"] if isinstance(s, dict)]
            else:
                inner_specs = [spec]

            built: list[Any] = []
            for inner in inner_specs:
                hook_type = (inner.get("type") or "command").lower()
                if hook_type == "command":
                    cmd = inner.get("command")
                    if not cmd:
                        continue
                    built.append(
                        CommandHook(
                            command=str(cmd),
                            timeout=int(inner.get("timeout", 60)),
                        ),
                    )
                elif hook_type == "prompt":
                    pmpt = inner.get("prompt")
                    if not pmpt:
                        continue
                    built.append(
                        PromptHook(
                            prompt=str(pmpt),
                            timeout=int(inner.get("timeout", 30)),
                        ),
                    )
                # WHY: function hooks can't come from JSON (they need a
                # Python callable). Silently skip rather than crash.
            if built:
                matchers.append(
                    HookMatcher(
                        hooks=built,
                        matcher=str(matcher) if matcher else None,
                        source="project",
                    ),
                )

    if not matchers:
        return None

    executor = HookExecutor()
    return HookEmitter(executor=executor, matchers=matchers)


# ---------------------------------------------------------------------------
# MCP tool loading (B-6)
# ---------------------------------------------------------------------------


def _load_mcp_tools(cwd: str) -> list[Any]:
    """Load + connect MCP servers from ``.mcp.json`` and ``~/.chimera/mcp.json``.

    Searches in this order (last wins on duplicate server names, ecosystem-parity):

    1. ``~/.chimera/mcp.json`` (user scope)
    2. ``<cwd>/.mcp.json`` (project scope)

    Returns the merged list of :class:`BaseTool` instances ready to add to
    an Agent's tool list. Empty list when no config or all loads fail.
    """
    candidates: list[Path] = [
        Path.home() / ".chimera" / "mcp.json",
        Path(cwd) / ".mcp.json",
    ]
    merged: dict[str, Any] = {"servers": {}}
    found_any = False
    for path in candidates:
        if not path.exists():
            continue
        found_any = True
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"[mink] warning: could not parse {path} ({exc}); skipping",
                file=sys.stderr,
            )
            continue
        servers = data.get("servers") or data.get("mcpServers") or {}
        if isinstance(servers, dict):
            merged["servers"].update(servers)
    if not found_any or not merged["servers"]:
        return []
    try:
        from chimera.mcp.tools import MCPToolSource

        _client, tools = MCPToolSource.from_config(merged)
        return list(tools)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[mink] warning: MCP server load failed ({exc}); "
            "running without MCP tools",
            file=sys.stderr,
        )
        return []


# ---------------------------------------------------------------------------
# One-shot --print path
# ---------------------------------------------------------------------------


def _make_run_id() -> str:
    """Generate a sortable, unique run id for a persisted ``-p`` invocation.

    The id is ``mink-<utc_compact>-<uuid8>`` (e.g.
    ``mink-20260424T013012-a3f9b1c2``). The compact UTC timestamp keeps
    lexical ordering aligned with chronological ordering, while the uuid
    suffix avoids collisions when two runs land in the same second.

    Returns:
        A new run id string.
    """
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return f"mink-{stamp}-{suffix}"


def _eventlog_root() -> Path:
    """Root directory for all persisted mink runs.

    Returns:
        ``~/.chimera/eventlog/`` honoring the current ``Path.home()``.
    """
    return Path.home() / ".chimera" / "eventlog"


def _open_run_log(run_id: str | None) -> tuple[Any, Path]:
    """Open (or create) an :class:`EventLog` for ``run_id``.

    Args:
        run_id: The persisted run identifier. When ``None``, a fresh id
            is minted via :func:`_make_run_id` so callers can pass
            ``getattr(args, "run_id", None)`` without a None-narrow first.

    Returns:
        A tuple of ``(EventLog, run_dir)``.
    """
    # WHY (pyright A1): accept Optional so callsites that read ``run_id``
    # off argparse Namespace don't need a separate narrowing branch.
    from chimera.sessions.eventlog.log import EventLog

    resolved = run_id or _make_run_id()
    run_dir = _eventlog_root() / resolved
    run_dir.mkdir(parents=True, exist_ok=True)
    return EventLog(run_dir), run_dir


def _append_user_message(log: Any, content: str) -> None:
    """Mirror ``EventSourcedSession.chat`` user-message bookkeeping.

    Using the same ``user_message`` event type lets ``EventSourcedSession.resume``
    rebuild the conversation later via ``--resume <id>``.
    """
    from chimera.events.base import Event

    log.append(Event(type="user_message", metadata={"content": content}))


def _append_agent_result(log: Any, result: Any) -> None:
    """Mirror ``EventSourcedSession.chat`` agent-result bookkeeping."""
    from chimera.events.base import Event

    log.append(
        Event(
            type="agent_result",
            metadata={
                "output": getattr(result, "output", ""),
                "steps": getattr(result, "steps", 0),
                "tool_calls_total": getattr(result, "tool_calls_total", 0),
                "cost": getattr(result, "cost", 0.0),
                "success": getattr(result, "success", False),
                "error": getattr(result, "error", None),
            },
        )
    )


def _write_run_summary(
    run_dir: Path,
    *,
    run_id: str,
    started_at: str,
    ended_at: str,
    model: str,
    prompt: str,
    result: Any,
    cwd: str,
    permission_mode: str,
) -> Path:
    """Write a ``summary.json`` next to the eventlog for quick inspection.

    The schema mirrors the lightweight ledger the interactive REPL writes
    so consumers can treat both the same. Cost and step counts are read
    off the ``AgentResult`` dataclass returned by ``agent.async_run``.

    Args:
        run_dir: The run's eventlog directory.
        run_id: The persisted run id.
        started_at: ISO-8601 UTC timestamp of run start.
        ended_at: ISO-8601 UTC timestamp of run completion.
        model: Provider model name actually used (post-fallback).
        prompt: The user prompt this run was driven by.
        result: The :class:`AgentResult` from ``agent.async_run``.
        cwd: Working directory the run executed against.
        permission_mode: The CLI ``--permission-mode`` value.

    Returns:
        The path to the written ``summary.json``.
    """
    payload = {
        "run_id": run_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "model": model,
        "prompt": prompt,
        "cwd": cwd,
        "permission_mode": permission_mode,
        "steps": int(getattr(result, "steps", 0) or 0),
        "tool_calls_total": int(getattr(result, "tool_calls_total", 0) or 0),
        "success": bool(getattr(result, "success", False)),
        "cost_usd": float(getattr(result, "cost", 0.0) or 0.0),
        # WHY: total_tokens isn't tracked on AgentResult today; keep the
        # key for forward-compat but report 0 when unknown rather than
        # omitting it (consumers can rely on schema stability).
        "total_tokens": 0,
        "error": getattr(result, "error", None),
    }
    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return summary_path


def _utc_iso8601() -> str:
    """ISO-8601 UTC timestamp with second precision and ``Z`` suffix."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _announce_saved_run(run_id: str, run_dir: Path) -> None:
    """Print the persisted-run pointer to stderr (one line, never to stdout)."""
    sys.stderr.write(
        f"[mink] run saved as {run_id} at {run_dir}/\n"
    )
    sys.stderr.flush()


def _run_print_mode(args: argparse.Namespace) -> int:
    """Execute a single turn and emit results in the requested format."""
    import asyncio

    from chimera.core.agent import Agent
    from chimera.core.cancellation import CancellationToken
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.message_queue import MessageQueues
    from chimera.core.prompt import Prompt
    from chimera.core.tool_group import AGENT_TOOLS

    cwd = os.path.abspath(args.cwd or os.getcwd())

    # WHY (audit H-6): resolve --agent <name> before provider construction so
    # an agent-supplied model (e.g. ``model: glm-5.1:cloud`` in frontmatter)
    # is honored when the user did not explicitly pass --model. Failure to
    # resolve a named agent is fatal and prints a clear stderr hint pointing
    # at the search paths so users can debug typos.
    agent_spec: _ResolvedAgentSpec | None = None
    if getattr(args, "agent", None):
        agent_spec = _resolve_agent_spec(args.agent, Path(cwd))
        if agent_spec is None:
            print(
                f"error: agent '{args.agent}' not found in .claude/agents/, "
                "~/.claude/agents/, or built-in registry. "
                "Built-in presets: build, explore, general, plan, review.",
                file=sys.stderr,
            )
            return 2

    # WHY: distinguish "user explicitly chose" (CLI flag or env var) from
    # "fell through to built-in default" so an agent preset's model can win
    # only against the latter. $CHIMERA_MINK_MODEL counts as user choice.
    env_model = os.environ.get("CHIMERA_MINK_MODEL")
    user_passed_model = args.model != _DEFAULT_MODEL or bool(env_model)
    effective_model = (
        args.model
        if user_passed_model or agent_spec is None or not agent_spec.model
        else agent_spec.model
    )
    provider = _build_provider(effective_model)
    # WHY (issue #127): when --remote is set, route file/bash tools through
    # SSHEnvironment instead of the local filesystem. setup() runs the
    # remote reachability probe here so we fail fast before the agent loop.
    env = _build_environment(args, cwd)
    env.setup()

    cancel = CancellationToken()
    queues = MessageQueues()
    # WHY (audit B-2 / B-7 / B-8): text mode now picks the right handler via
    # ``build_stream_handler`` — RichStreamHandler on a TTY (rendered Markdown +
    # spinner + collapsed tool blocks), plain ConsoleStreamHandler when piped
    # or when the user explicitly opts out via --no-rich/--no-color/$NO_COLOR.
    # JSON / stream-json modes still get None so structured output is clean.
    handler: Any = None
    if args.output_format == "text":
        from chimera.cli.render import build_stream_handler

        handler = build_stream_handler(
            no_color=bool(getattr(args, "no_rich", False))
            or bool(getattr(args, "no_color", False)),
        )

    # WHY (audit B-4 first half): load .claude/settings.json so allow/ask/deny
    # rules and the default permission mode are honored by the live one-shot.
    # Explicit --permission-mode flags ALWAYS win over the settings file so
    # users can override on the command line; settings.json is consulted only
    # when --permission-mode is left at its default ("default") and the file
    # actually contains rules. Hooks (audit B-4 second half) are loaded
    # unconditionally — they're orthogonal to permission-mode.
    settings_permissions: Any = None
    settings_hooks: dict[str, list[dict[str, Any]]] = {}
    user_passed_explicit_mode = args.permission_mode != "default"
    try:
        from chimera.mink.settings import load_mink_settings

        settings = load_mink_settings(cwd=Path(cwd))
        if not user_passed_explicit_mode and (
            settings.permissions.allow
            or settings.permissions.ask
            or settings.permissions.deny
        ):
            settings_permissions = settings.to_chimera_loop_config().permissions
        settings_hooks = dict(settings.hooks or {})
    except Exception as exc:  # noqa: BLE001
        print(
            f"[mink] warning: failed to load settings.json ({exc}); "
            "using --permission-mode default and no hooks",
            file=sys.stderr,
        )

    permissions_policy = settings_permissions or _policy_for_mode(args.permission_mode)
    # WHY (audit B-4 second half): translate the hooks block into a
    # HookEmitter so PreToolUse / PostToolUse / etc. fire end-to-end during
    # the one-shot run. Empty / missing hooks block returns None which
    # preserves the prior no-hooks behavior exactly.
    hook_emitter = _build_hook_emitter(settings_hooks)
    # WHY (audit H-4): plumb --tool-timeout into LoopConfig so the async
    # tool executor can wrap each dispatch with `asyncio.wait_for`. ``None``
    # (the default) preserves the prior unbounded behavior exactly.
    tool_timeout_s = getattr(args, "tool_timeout", None)
    config = LoopConfig(
        handler=handler,
        cancellation=cancel,
        message_queues=queues,
        permissions=permissions_policy,
        hook_emitter=hook_emitter,
        tool_timeout_s=tool_timeout_s,
    )
    loop = ReAct(max_steps=args.max_steps, config=config)

    # WHY (B-5): pull CLAUDE.md walk-up memory into the system prompt so
    # the live agent actually sees project + user instructions. Empty
    # memory is a no-op.
    # WHY (H-6): when --agent was resolved to a non-empty markdown body,
    # use that body as the system prompt so the user-supplied agent
    # actually shapes the run. Empty bodies fall back to the default.
    if agent_spec is not None and agent_spec.system_prompt.strip():
        base_prompt = agent_spec.system_prompt
    else:
        base_prompt = (
            "You are Mink, a Chimera coding agent. Use tools to inspect and "
            "modify the user's repo. Plan briefly, then act."
        )
    try:
        from chimera.context.agent_memory import load_memory

        memory_text = load_memory(cwd=Path(cwd))
    except Exception:  # noqa: BLE001
        memory_text = ""
    if memory_text:
        base_prompt = (
            base_prompt
            + "\n\n<memory source=\"CLAUDE.md\">\n"
            + memory_text
            + "</memory>"
        )
    prompt = Prompt.from_string(base_prompt)

    # WHY (H-6): when --agent specifies a tool list, constrain AGENT_TOOLS to
    # just those names. We map by tool.name (the canonical identifier the
    # provider sees) and lower-case both sides so frontmatter like
    # ``tools: [Bash]`` matches ``BashTool.name == "bash"``. Unknown tool
    # names are warned about but never fatal.
    tools = list(AGENT_TOOLS)
    if agent_spec is not None and agent_spec.tools:
        wanted_agent = {n.strip().lower() for n in agent_spec.tools if n.strip()}
        kept_agent = [t for t in tools if t.name.lower() in wanted_agent]
        unknown_agent = wanted_agent - {t.name.lower() for t in tools}
        if unknown_agent:
            print(
                f"[mink] warning: agent '{agent_spec.name}' references "
                f"unknown tool(s): {', '.join(sorted(unknown_agent))}",
                file=sys.stderr,
            )
        if kept_agent:
            tools = kept_agent

    # WHY (audit M-22): honor --allowed-tools when provided. Comma-separated
    # tool names, case-insensitive (so ``Bash`` matches ``bash``). Empty
    # string = no filter. An unknown name is fatal — exit 2 with the valid
    # tool list on stderr so users see a typo immediately. Pre-fix the flag
    # was parsed but the previous H-5 patch only warned, contradicting the
    # ecosystem-parity contract the help text advertises.
    allowed = (getattr(args, "allowed_tools", "") or "").strip()
    if allowed:
        try:
            tools = _filter_allowed_tools(tools, allowed)
        except _UnknownAllowedTool as exc:
            print(str(exc), file=sys.stderr)
            env.cleanup()
            return 2

    # WHY (B-6): if a project ``.mcp.json`` or user ``~/.chimera/mcp.json``
    # exists and declares servers, load + connect them and add their tools
    # to the agent. Best-effort: failures print a warning but never abort
    # the run (the user may simply have an old/broken config).
    mcp_tools = _load_mcp_tools(cwd)
    if mcp_tools:
        tools.extend(mcp_tools)

    agent = Agent(provider=provider, tools=tools, loop=loop, prompt=prompt)

    # WHY: open the eventlog *before* dispatching to stream-json so the
    # streaming path can reuse the same run id and writers, and we record
    # the user prompt as the first event regardless of output mode.
    save_enabled = not getattr(args, "no_save", False)
    run_id: str | None = None
    run_dir: Path | None = None
    log: Any | None = None
    started_at = _utc_iso8601()
    if save_enabled:
        run_id = getattr(args, "run_id", None) or _make_run_id()
        log, run_dir = _open_run_log(run_id)
        _append_user_message(log, args.print_mode)

    if args.output_format == "stream-json":
        return _run_stream_json(
            agent,
            env,
            args.print_mode,
            cancel,
            log=log,
            run_id=run_id,
            run_dir=run_dir,
            started_at=started_at,
            model=provider.model_name,
            cwd=cwd,
            permission_mode=args.permission_mode,
        )

    result: Any = None
    try:
        result = asyncio.run(agent.async_run(args.print_mode, env=env))
    except KeyboardInterrupt:
        cancel.cancel()
        print("\n[cancelled]", file=sys.stderr)
        return 130
    finally:
        env.cleanup()

    # Persist the result + summary even on partial failure so users can
    # inspect what the agent attempted before bailing out.
    if save_enabled and log is not None and run_dir is not None and run_id is not None:
        if result is not None:
            _append_agent_result(log, result)
        _write_run_summary(
            run_dir,
            run_id=run_id,
            started_at=started_at,
            ended_at=_utc_iso8601(),
            model=provider.model_name,
            prompt=args.print_mode,
            result=result if result is not None else _EmptyResult(),
            cwd=cwd,
            permission_mode=args.permission_mode,
        )
        _announce_saved_run(run_id, run_dir)

    if args.output_format == "json":
        # WHY: hand-roll a JSON view rather than dumping the result dataclass
        # directly — the loop result includes Message objects that aren't
        # natively JSON-serializable.
        payload = {
            "output": getattr(result, "output", ""),
            "steps": getattr(result, "steps", 0),
            "cost": getattr(result, "cost", 0.0),
            "success": getattr(result, "success", False),
            "model": provider.model_name,
        }
        if save_enabled and run_id is not None:
            payload["run_id"] = run_id
        json.dump(payload, sys.stdout)
        sys.stdout.write("\n")
    else:  # text
        out = getattr(result, "output", None)
        if out:
            print(out)
    return 0 if getattr(result, "success", False) else 1


@dataclasses.dataclass
class _EmptyResult:
    """Stand-in for an :class:`AgentResult` when the run aborted pre-completion."""

    output: str = ""
    steps: int = 0
    tool_calls_total: int = 0
    cost: float = 0.0
    success: bool = False
    error: str | None = "aborted"


# WHY (audit M-22): --allowed-tools must filter AGENT_TOOLS deterministically.
# Extracting the filter + the unknown-tool error keeps both the production
# flow in ``_run_print_mode`` and the regression tests in
# ``tests/mink/test_allowed_tools_flag.py`` calling the same code path.


class _UnknownAllowedTool(ValueError):
    """Raised when --allowed-tools names a tool that doesn't exist.

    Carrying the formatted error message on the exception keeps callers
    free of presentation logic — they ``print(exc)`` and exit 2.
    """


def _filter_allowed_tools(tools: list[Any], allowed: str) -> list[Any]:
    """Return *tools* filtered to the comma-separated names in *allowed*.

    Matching is case-insensitive so frontmatter-style ``Bash,Read`` matches
    the canonical lower-case ``BashTool.name``. An unknown name raises
    :class:`_UnknownAllowedTool` carrying the formatted error message
    callers should print before exiting 2.

    Args:
        tools: Source tool list (typically a list view of ``AGENT_TOOLS``
            after agent-spec narrowing).
        allowed: Raw comma-separated string from ``--allowed-tools``.
            Empty / whitespace-only entries are ignored.

    Returns:
        A new list containing the entries from *tools* whose ``.name``
        appears (case-insensitively) in *allowed*. Empty allowed string
        returns *tools* unchanged.

    Raises:
        _UnknownAllowedTool: When *allowed* names a tool not in *tools*.
    """
    cleaned = (allowed or "").strip()
    if not cleaned:
        return list(tools)
    wanted = {n.strip().lower() for n in cleaned.split(",") if n.strip()}
    if not wanted:
        return list(tools)
    name_index = {t.name.lower(): t for t in tools}
    unknown = sorted(wanted - set(name_index.keys()))
    if unknown:
        valid = ", ".join(sorted(name_index.keys()))
        raise _UnknownAllowedTool(
            f"error: unknown tool '{unknown[0]}'. Valid tools: {valid}"
        )
    return [t for name, t in name_index.items() if name in wanted]


def _build_stream_redaction() -> Any:
    """Build a :class:`RedactionMiddleware` for the stream-json output flow.

    Wires the live :class:`SecretRegistry` (seeded from the ambient env vars
    that hold provider API keys) plus a pattern :class:`SecretDetector` so
    both registered secrets *and* high-confidence pattern hits get scrubbed
    before any line lands on stdout.

    Returns:
        A configured :class:`RedactionMiddleware` instance.
    """
    # WHY (audit M-10): the previous _run_stream_json wrote raw json.dumps
    # straight to stdout, so any tool-call payload containing a secret leaked
    # verbatim. Centralising the middleware build here lets callers (incl.
    # tests) inject extra registered secrets through a thin closure if
    # needed without touching the redaction internals.
    from chimera.secrets.detector import SecretDetector
    from chimera.secrets.redactor import RedactionMiddleware
    from chimera.secrets.registry import SecretRegistry

    registry = SecretRegistry()
    registry.register_from_env(
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    )
    return RedactionMiddleware(
        registry=registry,
        detector=SecretDetector(),
        detect_unknown=True,
    )


def _redact_stream_line(line: dict[str, Any], middleware: Any) -> dict[str, Any]:
    """Apply *middleware* to *line* and return the redacted dict.

    The mink stream-json schema is ``{"type", "turn", "data"}`` — bespoke,
    not a real :class:`Event` subclass. We synthesize a temporary
    :class:`Event` whose ``metadata`` carries ``data`` so the existing
    middleware (which walks ``metadata`` recursively) can scrub it without
    needing a new code path. The wrapper is discarded after extraction so
    the on-wire schema is unchanged.

    Args:
        line: The raw stream-json dict about to be written.
        middleware: A :class:`RedactionMiddleware` instance.

    Returns:
        A new dict with the same shape as *line* but with secrets in
        ``data`` (and any nested strings) replaced by the placeholder.
    """
    from chimera.events.base import Event

    data = line.get("data")
    # WHY: only the ``data`` payload is user-influenced; the ``type`` /
    # ``turn`` keys are static enums controlled by the loop. Wrapping data
    # in metadata keeps the middleware's recursive container walk applicable
    # without re-implementing it here.
    wrapper = Event(type="_mink_stream_line", metadata={"data": data})
    redacted_holder: dict[str, Any] = {}

    def _capture(evt: Event) -> None:
        redacted_holder["data"] = evt.metadata.get("data")

    middleware.process(wrapper, _capture)
    return {**line, "data": redacted_holder.get("data", data)}


def _run_stream_json(
    agent: Any,
    env: Any,
    prompt: str,
    cancel: Any,
    *,
    log: Any | None = None,
    run_id: str | None = None,
    run_dir: Path | None = None,
    started_at: str | None = None,
    model: str = "",
    cwd: str = "",
    permission_mode: str = "default",
    redaction: Any = None,
) -> int:
    """Stream one JSON line per ``LoopEvent`` to stdout.

    M4-C may upgrade this to a richer schema; for now it just dumps the
    event ``type`` and a best-effort string view of ``data`` so that a
    downstream consumer can parse turn boundaries.

    The optional ``log``/``run_id``/``run_dir`` arguments turn this into
    a persisting run: the prompt is already journaled by the caller, and
    we journal the final ``AgentResult`` plus a ``summary.json`` here.

    Args:
        redaction: Optional pre-built :class:`RedactionMiddleware`. When
            ``None`` (the default) the standard registry+detector pair is
            built lazily so secrets in tool call/result payloads never
            land on stdout. Tests inject a custom middleware to register
            extra fake secrets without touching the env.
    """
    import asyncio

    # WHY (audit M-10): wire RedactionMiddleware into every emitted line so
    # tool-call payloads containing API keys / bearer tokens / etc. are
    # scrubbed before stdout. Building it once amortises the regex compile
    # across all events in the run.
    if redaction is None:
        redaction = _build_stream_redaction()

    def _emit(line: dict[str, Any]) -> None:
        scrubbed = _redact_stream_line(line, redaction)
        sys.stdout.write(json.dumps(scrubbed) + "\n")
        sys.stdout.flush()

    last_result_holder: dict[str, Any] = {"value": None}

    async def _drive() -> int:
        last_success = False
        # WHY: prefer async_run_events (the AgentLoop event-stream API) so
        # callers actually see one JSON line per LoopEvent. If an Agent
        # subclass lacks it, fall back to async_run + a single synthetic
        # result line so the contract (>=1 JSON line) holds either way.
        events_method = getattr(agent, "async_run_events", None)
        if events_method is None:
            events_method = getattr(agent, "async_iter_events", None)
        try:
            if events_method is not None:
                try:
                    async for event in events_method(prompt, env=env):
                        line = {
                            "type": getattr(event.type, "value", str(event.type)),
                            "turn": getattr(event, "turn", 0),
                            "data": _safe_event_data(event.data),
                        }
                        _emit(line)
                        if line["type"] == "result":
                            last_success = bool(
                                getattr(event.data, "reason", "") != "error"
                            )
                            last_result_holder["value"] = event.data
                except AttributeError:
                    # WHY: events_method existed but blew up on attribute
                    # access midway — drop to legacy fallback below.
                    events_method = None
            if events_method is None:
                # WHY: legacy Agent lacks async_run_events; preserve the
                # one-line contract via async_run + synthetic result event.
                result = await agent.async_run(prompt, env=env)
                last_result_holder["value"] = result
                _emit({
                    "type": "result",
                    "turn": getattr(result, "steps", 0),
                    "data": {
                        "output": getattr(result, "output", ""),
                        "cost": getattr(result, "cost", 0.0),
                        "success": getattr(result, "success", False),
                    },
                })
                last_success = bool(getattr(result, "success", False))
        except KeyboardInterrupt:
            cancel.cancel()
            return 130
        except Exception as exc:  # noqa: BLE001
            # WHY (audit B-3): surface unexpected failures to the user
            # instead of silently exiting 0 with no stdout. The audit's
            # original repro showed the CLI eating async_run's exception.
            _emit({
                "type": "error",
                "turn": 0,
                "data": {"message": str(exc), "exception": type(exc).__name__},
            })
            return 1
        return 0 if last_success else 1

    try:
        rc = asyncio.run(_drive())
    finally:
        env.cleanup()

    # WHY: write the agent_result + summary.json after the stream closes
    # so callers see the same on-disk shape as the text/json output modes.
    if (
        log is not None
        and run_dir is not None
        and run_id is not None
        and started_at is not None
    ):
        result = last_result_holder["value"]
        if result is not None:
            _append_agent_result(log, result)
        _write_run_summary(
            run_dir,
            run_id=run_id,
            started_at=started_at,
            ended_at=_utc_iso8601(),
            model=model,
            prompt=prompt,
            result=result if result is not None else _EmptyResult(),
            cwd=cwd,
            permission_mode=permission_mode,
        )
        _announce_saved_run(run_id, run_dir)
    return rc


def _safe_event_data(data: Any) -> Any:
    """Best-effort JSON view of arbitrary ``LoopEvent.data`` payloads."""
    if data is None or isinstance(data, (str, int, float, bool)):
        return data
    # ``is_dataclass(...)`` is True for both classes and instances; mypy
    # narrows it to DataclassInstance | type[...]. asdict() requires an
    # instance, so guard with ``not isinstance(data, type)``.
    if dataclasses.is_dataclass(data) and not isinstance(data, type):
        try:
            return dataclasses.asdict(data)
        except Exception:  # noqa: BLE001
            return repr(data)
    if isinstance(data, dict):
        return {k: _safe_event_data(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [_safe_event_data(v) for v in data]
    return repr(data)


# ---------------------------------------------------------------------------
# Interactive REPL — reuse `chimera code`
# ---------------------------------------------------------------------------


def _shim_code_args(args: argparse.Namespace) -> argparse.Namespace:
    """Translate mink-subcommand flags into the namespace ``run_code`` expects.

    ``run_code`` reads attributes off ``args`` directly (``model``,
    ``workdir``, ``max_steps``, ``mode``, ``models``, ``preset``,
    ``print_mode``). We construct a fresh namespace with those names
    populated from our flag set so we can reuse the REPL unchanged.
    """
    cwd = os.path.abspath(args.cwd or os.getcwd())
    return argparse.Namespace(
        model=args.model,
        workdir=cwd,
        max_steps=args.max_steps,
        mode="interactive",
        models="",
        preset=args.agent,  # WHY: --agent maps onto the CodingAgent preset slot
        print_mode=None,
    )


def _apply_permission_mode(args: argparse.Namespace) -> None:
    """Apply ``--permission-mode`` via env so downstream code can read it.

    The interactive REPL (``run_code``) doesn't yet take a permission-mode
    argument directly (M2 will wire ``mink/settings.py``); for M1 we expose
    the choice through ``CHIMERA_PERMISSION_MODE`` so anything that already
    reads it picks it up.
    """
    try:
        mode = _permission_mode_to_enum(args.permission_mode)
    except KeyError:
        return
    os.environ["CHIMERA_PERMISSION_MODE"] = mode.value


def _resolve_resume_workdir(args: argparse.Namespace) -> None:
    """If ``--resume <id>`` points at an existing session, prefer that workdir.

    Sessions live at ``~/.chimera/sessions/<sha256(workdir)[:12]>.jsonl``;
    we can't reverse the hash, so we just verify the file exists and let
    ``run_code`` rebuild against the same hash from the resolved cwd.
    """
    if not args.resume:
        return
    path = _resume_path(args.resume)
    if path is None:
        print(
            f"[warn] no saved session matches id '{args.resume}' "
            f"(looked at {Path.home() / '.chimera' / 'sessions'})",
            file=sys.stderr,
        )


def _apply_launch_resume(args: argparse.Namespace) -> int:
    """Resume the session named by ``--resume <id>`` before the REPL starts.

    Loads the saved session via :class:`EventSourcedSession.resume` (preferred)
    or :class:`Session.resume` (FileStorage fallback) and replays its
    messages into the SessionTree JSONL at the workdir-hash path the
    interactive REPL uses, so ``run_code`` will pick them up unchanged.

    Args:
        args: Parsed CLI namespace.

    Returns:
        Number of messages successfully restored. ``0`` if ``--resume``
        was unset or no session matched.
    """
    if not getattr(args, "resume", None):
        return 0
    sid = args.resume

    # WHY (audit M-17): Session.resume + EventSourcedSession.resume now both
    # accept ``SessionResumeAgent`` (a Protocol over ``prompt.render`` and
    # ``tools``). One minimal stub satisfies both paths without any cast.
    stub_agent = _ResumeAgentShim()

    # Try Session.resume against FileStorage first — this is the path
    # ``/resume`` inside the REPL also uses, keeping the surface uniform.
    try:
        from chimera.sessions.session import Session
        from chimera.sessions.storage.file import FileStorage

        storage = FileStorage()
        restored = Session.resume(
            session_id=sid,
            agent=stub_agent,
            storage=storage,
        )
        messages = list(restored.messages)
    except ValueError:
        # Try EventSourcedSession path before giving up.
        eventlog_root = Path.home() / ".chimera" / "eventlog"
        if not (eventlog_root / sid).exists():
            print(f"[resume] session '{sid}' not found", file=sys.stderr)
            return 0
        try:
            from chimera.sessions.eventlog.session import EventSourcedSession

            restored_es = EventSourcedSession.resume(
                log_dir=eventlog_root,
                session_id=sid,
                agent=stub_agent,
            )
            messages = list(restored_es.messages)
        except Exception as exc:  # noqa: BLE001
            print(f"[resume] failed: {exc}", file=sys.stderr)
            return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[resume] failed: {exc}", file=sys.stderr)
        return 0

    if not messages:
        print(f"[resume] session '{sid}' was empty", file=sys.stderr)
        return 0

    # Replay restored messages into the SessionTree JSONL the REPL reads.
    cwd = os.path.abspath(args.cwd or os.getcwd())
    tree_path = (
        Path.home() / ".chimera" / "sessions"
        / f"{_workdir_hash(cwd)}.jsonl"
    )
    tree_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from chimera.sessions.tree import SessionTree

        tree = SessionTree(tree_path)
        for msg in messages:
            tree.add_message(msg)
    except Exception as exc:  # noqa: BLE001
        print(f"[resume] could not stage tree at {tree_path}: {exc}",
              file=sys.stderr)
        return 0

    print(f"[resume] restored {len(messages)} messages from session {sid}",
          file=sys.stderr)
    return len(messages)


def _install_compaction_listener() -> None:
    """Subscribe to ``CompactionEvent`` on the global EventBus.

    When the AgentLoop fires a compaction (auto-compact), prints a single
    one-line system message to stderr in the form
    ``[auto-compact] N -> M tokens (Δ K%)``.
    """
    try:
        from chimera.events import EventBus, get_event_bus  # type: ignore[attr-defined]
        bus = get_event_bus()
    except Exception:
        try:
            from chimera.events.base import EventBus  # type: ignore[no-redef]

            # No global bus accessor — fall back to a fresh bus that the
            # LoopConfig hooks may pick up if pre-wired in env.
            bus = EventBus()
            os.environ.setdefault(
                "CHIMERA_EVENT_BUS_AVAILABLE", "0",
            )
        except Exception:
            return

    def _on_compaction(event: Any) -> None:
        meta = getattr(event, "metadata", {}) or {}
        before = (
            meta.get("messages_before")
            or getattr(event, "messages_before", None)
            or meta.get("tokens_before")
            or 0
        )
        after = (
            meta.get("messages_after")
            or getattr(event, "messages_after", None)
            or meta.get("tokens_after")
            or 0
        )
        delta_pct = 0.0
        if before:
            delta_pct = (before - after) / before * 100.0
        sys.stderr.write(
            f"[auto-compact] {before} -> {after} tokens "
            f"(Δ {delta_pct:.0f}%)\n"
        )
        sys.stderr.flush()

    try:
        bus.subscribe("compaction", _on_compaction)
    except Exception:  # noqa: BLE001
        # Subscribe API mismatch — fail silently rather than break the REPL.
        pass


def _slash_command_registry() -> dict[str, Any]:
    """Pull in the slash-command registry M1-C extracts.

    Falls back to ``chimera.cli.code._COMMANDS`` if the dedicated module
    hasn't landed yet so we never block on a sibling milestone.
    """
    try:
        from chimera.cli import slash_commands  # type: ignore[attr-defined]

        return getattr(slash_commands, "_COMMANDS", getattr(slash_commands, "COMMANDS", {}))
    except ImportError:
        from chimera.cli.code import _COMMANDS

        return _COMMANDS


# ---------------------------------------------------------------------------
# Subcommand entry
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# `runs list` / `runs show <id>` (audit H-3)
# ---------------------------------------------------------------------------


# WHY (cleanup A2): the legacy ``_list_run_dirs`` / ``_read_run_summary`` /
# ``_truncate`` helpers used to live here, but the canonical implementations
# now live in :mod:`chimera.mink.runs` (``iter_runs`` and ``format_run_*``).
# They were unused after that move, so they were deleted to remove dead code
# and avoid divergent on-disk schema readers.


def _run_runs_list(
    *,
    limit: int = 20,
    filter_model: str | None = None,
    success_only: bool = False,
    failed_only: bool = False,
    no_color: bool = False,
) -> int:
    """Implement ``chimera mink runs list``.

    Delegates the disk walk + formatting to :mod:`chimera.mink.runs` so the
    on-disk schema lives in one place. Filter flags shrink the result set
    before the table renders so the table accurately reflects the limit.

    Args:
        limit: Cap the number of rows shown after filtering. ``<=0`` means
            no cap.
        filter_model: When set, drop records whose ``model`` does not match
            this exact name.
        success_only: When True, drop records where ``success`` is False.
        failed_only: When True, drop records where ``success`` is True.
            Mutually exclusive with ``success_only``; if both are set,
            ``success_only`` wins (matches argparse last-write semantics).
        no_color: When True, suppress ANSI color regardless of TTY status.

    Returns:
        Exit code: ``0`` on success (including empty result set).
    """
    from chimera.mink.runs import format_run_table, iter_runs

    records = list(iter_runs())
    if filter_model:
        records = [r for r in records if r.model == filter_model]
    if success_only:
        records = [r for r in records if r.success]
    elif failed_only:
        records = [r for r in records if not r.success]

    color: bool | None = False if no_color else None
    out = format_run_table(records, limit=limit, color=color)
    print(out)
    return 0


def _run_runs_show(
    run_id: str | None,
    full: bool,
    *,
    show_events: bool = True,
    no_color: bool = False,
) -> int:
    """Implement ``chimera mink runs show <id>``.

    Args:
        run_id: The run directory name to inspect. ``None`` returns exit 2
            with a helpful error so missing args are distinguishable from
            "run not found" failures (which return exit 2 as well).
        full: Legacy flag; when True, force ``show_events=True`` so users
            of ``--full`` keep seeing the transcript.
        show_events: When False, suppress the transcript and print summary
            metadata only (driven by ``--no-events``).
        no_color: When True, suppress ANSI color.

    Returns:
        Exit code: ``0`` on success, ``2`` when the run id is missing or
        unknown (matches conventional Unix usage-error behavior).
    """
    from chimera.mink.runs import format_run_detail, get_run

    if not run_id:
        print(
            "error: 'mink runs show' requires a RUN_ID argument "
            "(see 'mink runs list' for available ids).",
            file=sys.stderr,
        )
        return 2

    try:
        detail = get_run(run_id)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        # Soft hint pointing at the directory the user can ls themselves.
        from chimera.mink.runs import default_eventlog_root

        print(
            f"hint: list available runs with 'chimera mink runs list' "
            f"(eventlog root: {default_eventlog_root()})",
            file=sys.stderr,
        )
        return 2

    color: bool | None = False if no_color else None
    print(
        format_run_detail(
            detail,
            color=color,
            include_events=bool(show_events or full),
        )
    )
    return 0


def _run_runs_cost(
    *,
    since: str | None,
    model: str | None,
    fmt: str,
    limit: int,
    no_color: bool = False,
) -> int:
    """Implement ``chimera mink runs cost``.

    Delegates aggregation + formatting to :mod:`chimera.mink.cost` so the
    on-disk schema stays in one place. ``--limit <= 0`` means "no cap".

    Args:
        since: Raw ``--since`` value (shorthand or ISO-8601 date).
        model: ``--runs-model`` filter; ``None`` / ``"all"`` keeps every model.
        fmt: ``"text"``, ``"json"``, or ``"csv"`` (validated by
            :func:`chimera.mink.cost.run_cost`).
        limit: ``--limit`` row cap. ``<=0`` is forwarded as ``None``.
        no_color: When True, suppress rich formatting and emit the plain
            ASCII table (driven by ``--no-color`` / ``--no-rich``).

    Returns:
        Exit code: ``0`` on success, ``2`` for usage errors (bad ``--since``
        or unknown ``--format``).
    """
    from chimera.mink.cost import run_cost

    rc, output = run_cost(
        since=since,
        model=model,
        fmt=fmt,
        limit=limit if limit and limit > 0 else None,
        use_rich=not no_color,
    )
    if rc == 0:
        print(output)
    else:
        print(output, file=sys.stderr)
    return rc


# ---------------------------------------------------------------------------
# `runs share <id>` (issue #129) — package an eventlog dir into a sharable URL.
# ---------------------------------------------------------------------------


def _run_runs_share(
    run_id: str | None,
    *,
    sink: str = "file",
) -> int:
    """Implement ``chimera mink runs share <id> [--sink ...]``.

    Delegates packaging to :func:`chimera.sessions.share.export_to_url`,
    then prints the resulting URL/path/data-URI to stdout. Errors land
    on stderr with a non-zero exit so shell pipelines fail loudly.

    Args:
        run_id: Directory name under ``~/.chimera/eventlog`` to share.
            ``None`` returns exit 2 with a usage hint.
        sink: One of ``"gist"``, ``"file"``, ``"base64"``. Validated by
            ``export_to_url``; we surface ``ValueError`` as exit 2.

    Returns:
        Exit code: ``0`` on success, ``2`` for usage / not-found errors,
        ``1`` for runtime failures (e.g. missing ``gh`` CLI).
    """
    from chimera.sessions.share import export_to_url

    if not run_id:
        print(
            "error: 'mink runs share' requires a RUN_ID argument "
            "(see 'mink runs list' for available ids).",
            file=sys.stderr,
        )
        return 2
    try:
        token = export_to_url(run_id, sink=sink)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(token)
    return 0


# ---------------------------------------------------------------------------
# `agents list` / `agents show <name>` (audit H-3-adjacent)
# ---------------------------------------------------------------------------


def _run_agents_list(*, no_color: bool = False) -> int:
    """Implement ``chimera mink agents list``.

    Delegates discovery + formatting to :mod:`chimera.mink.agents` so the
    project/user/built-in chain stays in one place. Always exits 0 — an
    empty result is still a valid (if surprising) answer.

    Args:
        no_color: When True, suppress ANSI color regardless of TTY status.

    Returns:
        Exit code: ``0`` on success.
    """
    from chimera.mink.agents import format_agents_table, iter_agents

    records = list(iter_agents())
    print(format_agents_table(records, no_color=no_color))
    return 0


def _run_agents_show(name: str | None, *, no_color: bool = False) -> int:
    """Implement ``chimera mink agents show <name>``.

    Resolves through the same project > user > built-in chain
    ``--agent <name>`` uses; on miss exits 2 with a friendly stderr hint
    pointing at the search paths so users can debug typos.

    Args:
        name: Agent name to show. ``None`` returns exit 2 with a usage hint.
        no_color: When True, suppress ANSI color.

    Returns:
        Exit code: ``0`` on success, ``2`` when the name is missing or
        unresolved.
    """
    from chimera.mink.agents import find_agent, format_agent_detail

    if not name:
        print(
            "error: 'mink agents show' requires an AGENT_NAME argument "
            "(see 'mink agents list' for available names).",
            file=sys.stderr,
        )
        return 2

    record = find_agent(name)
    if record is None:
        print(
            f"error: agent '{name}' not found in .claude/agents/, "
            "~/.claude/agents/, or built-in registry. "
            "Built-in presets: build, explore, general, plan, review.",
            file=sys.stderr,
        )
        return 2

    print(format_agent_detail(record, no_color=no_color))
    return 0


def _dispatch_agents(args: argparse.Namespace) -> int | None:
    """Return an exit code if ``args`` requests the agents subcommand.

    Returning ``None`` means "no agents subcommand asked for; caller
    proceeds with the normal mink dispatch path".
    """
    if getattr(args, "runs_command", None) != "agents":
        return None
    no_color = bool(getattr(args, "no_color", False) or getattr(args, "no_rich", False))
    action = getattr(args, "runs_action", None)
    if action == "list" or action is None:
        # Treat ``mink agents`` (no action) as ``mink agents list`` — the
        # common case is "what's available?", so default to the table view.
        return _run_agents_list(no_color=no_color)
    if action == "show":
        return _run_agents_show(getattr(args, "runs_target", None), no_color=no_color)
    print(
        f"error: unknown 'agents' action: {action!r} "
        "(supported: list, show)",
        file=sys.stderr,
    )
    return 2


def _dispatch_runs(args: argparse.Namespace) -> int | None:
    """Return an exit code if ``args`` requests the runs subcommand.

    Returning ``None`` means "no runs subcommand asked for; caller proceeds
    with the normal mink dispatch path". This keeps :func:`run` readable.
    """
    if getattr(args, "runs_command", None) != "runs":
        return None
    no_color = bool(getattr(args, "no_color", False) or getattr(args, "no_rich", False))
    action = getattr(args, "runs_action", None)
    if action == "list" or action is None:
        # Treat ``mink runs`` (no action) as ``mink runs list`` — friendlier
        # default than a usage error for the most common inspection.
        return _run_runs_list(
            limit=int(getattr(args, "runs_limit", 20) or 20),
            filter_model=getattr(args, "runs_filter_model", None),
            success_only=bool(getattr(args, "runs_success_only", False)),
            failed_only=bool(getattr(args, "runs_failed_only", False)),
            no_color=no_color,
        )
    if action == "show":
        return _run_runs_show(
            getattr(args, "runs_target", None),
            bool(getattr(args, "full", False)),
            show_events=bool(getattr(args, "runs_show_events", True)),
            no_color=no_color,
        )
    if action == "share":
        return _run_runs_share(
            getattr(args, "runs_target", None),
            sink=str(getattr(args, "runs_share_sink", "file") or "file"),
        )
    if action == "cost":
        return _run_runs_cost(
            since=getattr(args, "runs_cost_since", None),
            model=getattr(args, "runs_filter_model", None),
            fmt=str(getattr(args, "runs_cost_format", "text") or "text"),
            limit=int(getattr(args, "runs_limit", 0) or 0),
            no_color=no_color,
        )
    print(
        f"error: unknown 'runs' action: {action!r} "
        "(supported: list, show, share, cost)",
        file=sys.stderr,
    )
    return 2


def run(args: argparse.Namespace) -> int:
    """Entry point invoked by ``chimera mink``.

    Args:
        args: Parsed ``argparse.Namespace`` from the ``mink`` subparser.

    Returns:
        Process exit code (``0`` on success).
    """
    # WHY (audit H-3): the runs subcommand is read-only and never touches
    # the live agent / provider stack, so dispatch it first to bypass the
    # OllamaProvider bring-up in `_build_provider`. Returning early keeps
    # `mink runs list` snappy on a fresh machine without Ollama.
    runs_rc = _dispatch_runs(args)
    if runs_rc is not None:
        return runs_rc

    # WHY (audit H-3-adjacent): same shape for the ``agents`` subcommand —
    # purely read-only, no provider needed, exit early to keep listings
    # snappy and offline-friendly.
    agents_rc = _dispatch_agents(args)
    if agents_rc is not None:
        return agents_rc

    _apply_permission_mode(args)
    _resolve_resume_workdir(args)
    _apply_launch_resume(args)
    _install_compaction_listener()

    # WHY: import the registry eagerly so a missing slash_commands module
    # fails fast with a clear traceback instead of mid-REPL.
    _slash_command_registry()

    if args.print_mode is not None:
        return _run_print_mode(args)

    # Interactive: hand off to the existing REPL with a shimmed namespace.
    # We don't fork run_code's body so future REPL improvements (steering,
    # tree, /yolo, etc.) automatically reach `chimera mink` users.
    from chimera.cli.code import run_code

    # Pre-build an OllamaProvider once via our fallback path, then pass
    # the resolved model name through so run_code's create_provider call
    # picks the same tag. We can't inject the provider directly into
    # run_code without modifying it (M1-C scope), so model-name is the
    # contract for now.
    provider = _build_provider(args.model)
    shimmed = _shim_code_args(args)
    shimmed.model = provider.model_name
    return run_code(shimmed)


__all__ = ["add_arguments", "run"]


# ---------------------------------------------------------------------------
# Workdir hash helper (kept for resume/path tooling parity with code.py)
# ---------------------------------------------------------------------------


def _workdir_hash(workdir: str) -> str:
    """Stable id derived from a workdir path — matches ``code._session_path``."""
    return hashlib.sha256(workdir.encode()).hexdigest()[:12]
