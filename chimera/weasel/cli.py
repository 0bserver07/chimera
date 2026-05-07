"""``chimera weasel`` — minimal coding-agent harness CLI (W1 scaffold).

Weasel is the fourth Chimera coding-agent CLI. Where mink/otter/ferret each
ship rich opinionated ergonomics, weasel ships **powerful defaults + four
operating modes** and skips features like sub-agents and plan mode entirely.
Minimalism is the feature.

This module ships the W1 scaffold:

* ``add_arguments`` registers a deliberately tiny flag surface
  (``--version``, ``--mode``, ``--model``, ``-p``, ``--json``,
  ``--list-models``, plus a ``sessions`` subcommand placeholder for W1's
  list/show).
* ``run`` dispatches to the four documented modes — interactive (REPL),
  print, RPC, SDK passthrough — or the ``sessions`` subcommand.
* The three placeholder modes return ``2`` (usage) when their owners
  (W2 RPC, W4 SDK) haven't landed yet, so the scaffold never silently
  no-ops a request.

Trademark hygiene: never names the upstream brand. ``.weasel/extensions/``
is a filesystem fact (not a brand claim). The ``.weasel/`` mention here is
likewise a path, not a product name.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from chimera.errors import friendly_errors

# WHY: stdlib only at import time. The interactive path delegates to
# ``chimera.cli.code.run_code`` which itself lazy-imports providers — so
# ``chimera weasel --help`` / ``--version`` stays cheap even when the
# Anthropic / OpenAI SDKs aren't installed.

_VERSION = "0.5.0"
"""Weasel scaffold version. Independent of the chimera package version
because weasel is a per-CLI release line."""

_DEFAULT_MODEL = "claude-sonnet-4-6"
"""Default model when neither ``--model`` nor ``$WEASEL_MODEL`` is set."""

_VALID_MODES = ("interactive", "print", "rpc", "sdk")
_VALID_SUBCOMMANDS = (None, "sessions", "share")
_VALID_SUB_ACTIONS = (None, "list", "show", "cost")

# A10-W11: parser ref + per-flag long descriptions for ``--help-long``.
_PARSER: argparse.ArgumentParser | None = None
_LONG_HELP: dict[str, str] = {
    "--model": (
        "Model identifier. Resolution order: --model > $WEASEL_MODEL > "
        f"the {_DEFAULT_MODEL} default. Resolved through "
        "chimera.providers.factory.create_provider."
    ),
    "--mode": (
        "Operating mode: 'interactive' (REPL, default), 'print' "
        "(one-shot text/JSON), 'rpc' (stdio JSON-RPC), 'sdk' "
        "(prints embedding pointer and exits)."
    ),
    "-p / --print": (
        "One-shot print mode: run a single agent turn against PROMPT, "
        "emit the assistant text on stdout, then exit."
    ),
    "--json": (
        "When paired with -p, emit a single JSON object on stdout "
        "({output, success, model}) instead of plain text."
    ),
    "--list-models": (
        "List models recognised by chimera.providers.cost.PRICING and "
        "exit. Useful for discovering valid identifiers."
    ),
    "--cwd": (
        "Working directory for the agent run. Default: process cwd."
    ),
    "--max-steps": (
        "Maximum agent steps per turn (default: 50)."
    ),
    "--legacy-react": (
        "Opt out of the new CodingAgent default (wave 11) and use the "
        "legacy bare ReAct stack for free-text turns. Reserved for "
        "back-compat with users who need the original W5 behaviour."
    ),
    "--resume": (
        "Resume a persisted weasel run by id (matches "
        "~/.chimera/eventlog/<id>/). The replayed conversation is "
        "prepended to the new turn so the agent has full context."
    ),
    "-c / --continue": (
        "Resume the most-recent weasel run under the current working "
        "directory. Equivalent to --resume <newest-weasel-id-in-cwd>."
    ),
    "subcommand": (
        "Optional positional: 'sessions' (list/show/cost) or "
        "'share <id>' to export a session transcript."
    ),
    "--since": (
        "With 'sessions cost': drop sessions older than this cutoff. "
        "Accepts shorthand (7d / 24h / 30m) or an ISO-8601 date."
    ),
    "--cost-model": (
        "With 'sessions cost': case-insensitive substring filter on "
        "model name. Pass 'all' (or omit) to include every model."
    ),
    "--cost-format": (
        "With 'sessions cost': output format. Defaults to 'json' "
        "when --json is set, 'text' otherwise. CSV is also supported."
    ),
    "--cost-limit": (
        "With 'sessions cost': cap on rows considered (newest first). "
        "No cap by default; useful for fixture stability."
    ),
    "--share-sink": (
        "With 'share': destination for the rendered transcript. "
        "Defaults to 'file' (writes ~/.chimera/shares/weasel-<id>.<ext>)."
    ),
    "--share-format": (
        "With 'share': render format. Defaults to 'json' "
        "(round-trips with 'sessions show --json'); 'md' yields a "
        "human-readable transcript."
    ),
    "--theme": (
        "Theme name. Resolved from built-ins (default, dark, "
        "solarized) plus on-disk JSON files under <cwd>/.weasel/"
        "themes/ and ~/.weasel/themes/. Defaults to $WEASEL_THEME "
        "or 'default'."
    ),
    "--prompt-template": (
        "Prompt-template name. Resolved from the built-in 'default' "
        "plus on-disk markdown files under <cwd>/.weasel/prompts/ "
        "and ~/.weasel/prompts/. Defaults to $WEASEL_PROMPT_TEMPLATE "
        "or 'default'."
    ),
    "--all-clis": (
        "With 'sessions list': include sessions created by every "
        "Chimera CLI (otter / ferret / shrew / stoat / mink / "
        "badger), not just weasel. Adds an ORIGIN column."
    ),
}


def _resolve_version() -> str:
    """Return the weasel scaffold version string for ``--version``.

    Returns:
        ``"0.5.0"`` (the per-CLI release line) — independent of the
        ``chimera-run`` package version. Mirrors the four-mode harness's
        own per-CLI release cadence.
    """
    return _VERSION


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register ``chimera weasel`` flags on ``parser``.

    Mirrors mink/otter's ``add_arguments`` shape so embedders / tests can
    attach the same flag surface to a parser they already own.

    Args:
        parser: An :class:`argparse.ArgumentParser` (typically the weasel
            subparser created by :func:`chimera.cli.main.build_parser`).
    """
    # A10-W11: stash for ``--help-long`` rendering in ``run()``.
    global _PARSER
    _PARSER = parser

    parser.add_argument(
        "--version",
        action="version",
        version=f"chimera weasel {_resolve_version()}",
    )
    parser.add_argument(
        "--help-long",
        dest="help_long",
        action="store_true",
        default=False,
        help="Show full help (incl. long flag descriptions).",
    )

    core = parser.add_argument_group("Core")
    behavior = parser.add_argument_group("Behavior")
    output = parser.add_argument_group("Output")
    persistence = parser.add_argument_group("Persistence")

    # WHY: env precedence is --model > $WEASEL_MODEL > _DEFAULT_MODEL.
    core.add_argument(
        "--model",
        default=os.environ.get("WEASEL_MODEL") or _DEFAULT_MODEL,
        metavar="MODEL",
        help=f"Model id (default: $WEASEL_MODEL or {_DEFAULT_MODEL}).",
    )
    # WHY: the four-mode philosophy.
    core.add_argument(
        "--mode",
        choices=list(_VALID_MODES),
        default="interactive",
        metavar="MODE",
        help="interactive | print | rpc | sdk (default: interactive).",
    )
    core.add_argument(
        "-p",
        "--print",
        dest="print_mode",
        default=None,
        metavar="PROMPT",
        help="One-shot: run PROMPT, print, exit.",
    )
    output.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=False,
        help="With -p: emit JSON envelope instead of text.",
    )
    output.add_argument(
        "--list-models",
        dest="list_models",
        action="store_true",
        default=False,
        help="List recognised model ids and exit.",
    )
    core.add_argument(
        "--cwd",
        default=None,
        help="Working directory (default: cwd).",
    )
    behavior.add_argument(
        "--max-steps",
        type=int,
        default=50,
        metavar="N",
        help="Max agent steps per turn (default: 50).",
    )
    # WHY (B1, wave 11): align weasel with the wave-10 G3 default flip.
    behavior.add_argument(
        "--legacy-react",
        dest="legacy_react",
        action="store_true",
        default=False,
        help="Use legacy ReAct (opt out of CodingAgent default).",
    )
    # WHY (C1, wave 9): --resume / --continue mirror mink's flag pair.
    persistence.add_argument(
        "--resume",
        default=None,
        metavar="ID",
        help="Resume a persisted weasel run by id.",
    )
    persistence.add_argument(
        "-c",
        "--continue",
        dest="continue_latest",
        action="store_true",
        default=False,
        help="Resume the newest weasel run under cwd.",
    )
    parser.add_argument(
        "subcommand",
        nargs="?",
        default=None,
        choices=list(_VALID_SUBCOMMANDS),
        metavar="SUBCOMMAND",
        help="sessions | share.",
    )
    parser.add_argument(
        "sub_action",
        nargs="?",
        default=None,
        metavar="ACTION",
        help="list | show | cost | <session-id>.",
    )
    parser.add_argument(
        "sub_target",
        nargs="?",
        default=None,
        metavar="TARGET",
        help="Session id for sessions show.",
    )
    persistence.add_argument(
        "--since",
        dest="cost_since",
        default=None,
        metavar="WINDOW",
        help="sessions cost: cutoff (e.g. 7d / ISO).",
    )
    persistence.add_argument(
        "--cost-model",
        dest="cost_model",
        default=None,
        metavar="STR",
        help="sessions cost: model substring filter.",
    )
    persistence.add_argument(
        "--cost-format",
        dest="cost_format",
        choices=("text", "json", "csv"),
        default=None,
        metavar="FMT",
        help="sessions cost: text | json | csv.",
    )
    persistence.add_argument(
        "--cost-limit",
        dest="cost_limit",
        type=int,
        default=None,
        metavar="N",
        help="sessions cost: row cap (newest first).",
    )
    persistence.add_argument(
        "--share-sink",
        dest="share_sink",
        choices=("file", "stdout"),
        default=None,
        metavar="SINK",
        help="share: file (default) | stdout.",
    )
    persistence.add_argument(
        "--share-format",
        dest="share_format",
        choices=("json", "md"),
        default=None,
        metavar="FMT",
        help="share: json (default) | md.",
    )
    behavior.add_argument(
        "--theme",
        dest="theme",
        default=os.environ.get("WEASEL_THEME") or None,
        metavar="NAME",
        help="REPL theme (default: $WEASEL_THEME or 'default').",
    )
    behavior.add_argument(
        "--prompt-template",
        dest="prompt_template",
        default=os.environ.get("WEASEL_PROMPT_TEMPLATE") or None,
        metavar="NAME",
        help="System prompt template (default: 'default').",
    )
    # B9-W11: cross-CLI session listing.
    persistence.add_argument(
        "--all-clis",
        dest="sessions_all_clis",
        action="store_true",
        default=False,
        help="sessions list: include every Chimera CLI's sessions.",
    )


# ---------------------------------------------------------------------------
# --list-models
# ---------------------------------------------------------------------------


def _run_list_models() -> int:
    """Print known model identifiers (from ``chimera.providers.cost.PRICING``).

    Returns:
        Process exit code (always ``0``).
    """
    try:
        from chimera.providers.cost import PRICING
    except Exception as exc:  # noqa: BLE001 — never crash on import drift
        print(f"weasel: could not load model registry: {exc}", file=sys.stderr)
        return 1
    for model in sorted(PRICING):
        print(model)
    return 0


# ---------------------------------------------------------------------------
# print mode (one-shot)
# ---------------------------------------------------------------------------


def _activate_extensions(cwd: str) -> tuple[list[Any], list[Any]]:
    """Discover and activate weasel extensions for a single agent run.

    Walks ``<cwd>/.weasel/extensions/`` and ``~/.weasel/extensions/``
    via :func:`chimera.weasel.extensions.load_weasel_extensions`, then
    activates each plugin onto a fresh
    :class:`chimera.plugins.base.ComponentRegistry` so we can collect
    the tool + hook contributions. Plugin activation failures are
    swallowed: a single bad extension must not break the print path.

    Args:
        cwd: Project root used as the project-scope discovery anchor.

    Returns:
        ``(tools, hooks)`` — a list of :class:`BaseTool` instances and
        a list of :class:`Hook` records, both possibly empty.
    """
    from pathlib import Path

    from chimera.plugins.base import ComponentRegistry
    from chimera.weasel.extensions import load_weasel_extensions

    try:
        plugins = load_weasel_extensions(Path(cwd))
    except Exception as exc:  # noqa: BLE001 — never crash the print path
        print(
            f"weasel: extension discovery failed; continuing without "
            f"extensions: {exc}",
            file=sys.stderr,
        )
        return [], []

    registry = ComponentRegistry()
    for plugin in plugins:
        try:
            plugin.activate(registry)
        except Exception as exc:  # noqa: BLE001 — quarantine extension errors
            print(
                f"weasel: extension '{getattr(plugin, 'name', '?')}' failed "
                f"to activate: {exc}",
                file=sys.stderr,
            )
            continue

    tools = list(registry.tools)
    # ``ComponentRegistry.hooks`` is keyed by event type; flatten back to
    # a list of Hook records so downstream consumers see one record per
    # contribution.
    hooks: list[Any] = []
    for entries in registry.hooks.values():
        hooks.extend(entries)
    return tools, hooks


def _resolve_prompt_template(args: argparse.Namespace, cwd: str) -> Any:
    """Resolve ``--prompt-template`` to a :class:`PromptTemplate`.

    Walks the user/project prompt-template roots via
    :func:`chimera.weasel.prompt_templates.load_prompt_templates` and
    returns the named entry. Unknown / missing names fall back to the
    built-in ``default`` template so the print path always has a
    usable system prompt.

    Args:
        args: Parsed weasel CLI namespace; ``prompt_template`` is read
            off it as a ``str | None``.
        cwd: Project root used as the project-scope discovery anchor.

    Returns:
        A :class:`chimera.weasel.prompt_templates.PromptTemplate`.
    """
    from pathlib import Path

    from chimera.weasel.prompt_templates import (
        get_prompt_template,
        load_prompt_templates,
    )

    try:
        registry = load_prompt_templates(Path(cwd))
    except Exception as exc:  # noqa: BLE001 — never crash the print path
        print(
            f"weasel: prompt-template discovery failed; using built-in "
            f"default: {exc}",
            file=sys.stderr,
        )
        registry = None

    name = getattr(args, "prompt_template", None)
    return get_prompt_template(name, registry=registry)


def _resolve_theme(args: argparse.Namespace, cwd: str) -> Any:
    """Resolve ``--theme`` to a :class:`Theme`.

    Walks the user/project theme roots via
    :func:`chimera.weasel.themes.load_themes` and returns the named
    entry. Unknown / missing names fall back to the built-in
    ``default`` theme so callers always get a populated palette.

    Args:
        args: Parsed weasel CLI namespace; ``theme`` is read off it
            as a ``str | None``.
        cwd: Project root used as the project-scope discovery anchor.

    Returns:
        A :class:`chimera.weasel.themes.Theme`.
    """
    from pathlib import Path

    from chimera.weasel.themes import get_theme, load_themes

    try:
        registry = load_themes(Path(cwd))
    except Exception as exc:  # noqa: BLE001 — never crash the print path
        print(
            f"weasel: theme discovery failed; using built-in default: {exc}",
            file=sys.stderr,
        )
        registry = None

    name = getattr(args, "theme", None)
    return get_theme(name, registry=registry)


def _run_print_mode(args: argparse.Namespace) -> int:
    """Execute a single turn and emit results in the requested format.

    Builds a minimal :class:`Agent` directly (no MCP / LSP wiring, no
    rules ingestion, no checkpointing — that's the weasel point), then
    layers in any extensions discovered under
    ``<cwd>/.weasel/extensions/`` and ``~/.weasel/extensions/`` (W3).
    When ``--json`` is set, emits a single JSON object on stdout;
    otherwise prints the plain-text output.

    Args:
        args: Parsed CLI namespace from :func:`add_arguments`.

    Returns:
        Process exit code (``0`` on agent success, ``1`` otherwise).
    """
    import asyncio
    import json

    from chimera.core.agent import Agent
    from chimera.core.cancellation import CancellationToken
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.prompt import Prompt
    from chimera.core.tool_group import AGENT_TOOLS
    from chimera.env.local import LocalEnvironment
    from chimera.weasel.providers import build_provider as _build_provider

    cwd = os.path.abspath(args.cwd or os.getcwd())

    try:
        # WHY: weasel.providers.build_provider knows about the full chain
        # including Ollama tag detection (`name:tag` ids like glm-5.1:cloud)
        # and the OpenRouter / llama.cpp / Ollama fallbacks. The bare
        # ``create_provider`` factory only handles prefix-based inference
        # which routes ``glm-*`` to Anthropic and fails on Ollama-tagged ids.
        provider = _build_provider(args)
    except Exception as exc:  # noqa: BLE001 — surface provider auth errors cleanly
        print(f"weasel: provider error: {exc}", file=sys.stderr)
        return 1

    env = LocalEnvironment(workdir=cwd)
    env.setup()

    cancel = CancellationToken()
    # WHY: load extensions before constructing the loop so any
    # extension-contributed hooks land in the LoopConfig.hooks bag and
    # extension tools are merged into the agent's tool group below.
    # Extension load failures are swallowed (load_errors lives on the
    # extension instance) so a bad extension cannot break the print path.
    ext_tools, ext_hooks = _activate_extensions(cwd)

    config = LoopConfig(cancellation=cancel)
    loop = ReAct(max_steps=int(args.max_steps), config=config)
    # WHY (W2, wave 9): resolve --prompt-template (or $WEASEL_PROMPT_TEMPLATE)
    # before building the system prompt so users can override the stock
    # weasel instructions without editing source. Unknown / missing names
    # fall through to the built-in default template, whose body matches
    # the literal that used to live here verbatim.
    template = _resolve_prompt_template(args, cwd)
    prompt = Prompt.from_string(template.system_prompt)

    tools = list(AGENT_TOOLS) + ext_tools
    agent = Agent(
        provider=provider,
        tools=tools,
        loop=loop,
        prompt=prompt,
    )
    # WHY: stash hooks where downstream consumers can find them. The
    # core ReAct loop doesn't natively dispatch shell-command hooks
    # (that's a follow-up); attaching them to the agent makes them
    # introspectable via tests and future hook-runner middleware.
    if ext_hooks:
        try:
            setattr(agent, "_weasel_extension_hooks", ext_hooks)
        except Exception:  # noqa: BLE001 — defensive
            pass

    # WHY (W2, wave 9): a prompt template may declare a ``user_prefix``
    # that gets spliced in front of every user turn. We resolve it
    # here and concatenate before the resume-prefix wrapper so the
    # template prefix lands closest to the user's actual input.
    effective_user = args.print_mode
    if template.user_prefix:
        effective_user = f"{template.user_prefix}{effective_user}"

    # WHY (W2, wave 9): stash the resolved theme on the agent so
    # downstream renderers (REPL, future styled print formatters) can
    # introspect colors / prompt prefixes without re-resolving the
    # flag. The print path itself is plain stdout — we don't restyle
    # it here — but tests assert the theme propagates through.
    theme = _resolve_theme(args, cwd)
    try:
        setattr(agent, "_weasel_theme", theme)
    except Exception:  # noqa: BLE001 — defensive
        pass

    # WHY (C1, wave 9): apply ``--resume`` / ``-c`` before dispatching to
    # the agent so a one-shot run can pick up the prior weasel context.
    effective_prompt = _apply_weasel_resume_prefix(
        args, default_prompt=effective_user,
    )

    result: Any = None
    try:
        result = asyncio.run(agent.async_run(effective_prompt, env=env))
    except KeyboardInterrupt:
        cancel.cancel()
        print("\n[cancelled]", file=sys.stderr)
        return 130
    finally:
        env.cleanup()

    success = bool(getattr(result, "success", False))
    output = getattr(result, "output", "") or ""

    if getattr(args, "json_output", False):
        payload = {
            "output": output,
            "success": success,
            "model": getattr(provider, "model_name", args.model),
        }
        json.dump(payload, sys.stdout)
        sys.stdout.write("\n")
    else:
        if output:
            print(output)
    return 0 if success else 1


def _apply_weasel_resume_prefix(
    args: argparse.Namespace,
    *,
    default_prompt: str,
) -> str:
    """Resolve ``--resume`` / ``--continue`` for weasel.

    Symmetric helper to otter / ferret's resume-prefix wrappers. See
    :func:`chimera.sessions.eventlog.resume_helpers.resolve_resume_id`
    for the resolution semantics.

    Args:
        args: The parsed weasel argparse namespace.
        default_prompt: The user's ``-p`` text. Returned unchanged when
            no resume id resolves.

    Returns:
        Either ``default_prompt`` unchanged or the rendered transcript
        prefix concatenated with it.
    """
    from chimera.sessions.eventlog.resume_helpers import (
        build_resume_prefix,
        default_eventlog_root,
        resolve_resume_id,
        resume_run,
    )

    target_id = resolve_resume_id(
        explicit_id=getattr(args, "resume", None),
        continue_latest=bool(getattr(args, "continue_latest", False)),
        prefix="weasel-",
        eventlog_root=default_eventlog_root(),
        cwd=os.path.abspath(getattr(args, "cwd", None) or os.getcwd()),
    )
    if target_id is None:
        return default_prompt

    try:
        session = resume_run(target_id)
    except (ValueError, OSError) as exc:
        print(
            f"[weasel] --resume / --continue: failed to load run "
            f"{target_id!r}: {exc}",
            file=sys.stderr,
        )
        return default_prompt

    messages = list(getattr(session, "messages", []) or [])
    if not messages:
        return default_prompt

    sys.stderr.write(
        f"[weasel] resumed run {target_id} ({len(messages)} messages)\n"
    )
    sys.stderr.flush()
    transcript = build_resume_prefix(messages)
    return f"{transcript}{default_prompt}"


# ---------------------------------------------------------------------------
# RPC + SDK placeholders
# ---------------------------------------------------------------------------


def _run_rpc_mode(args: argparse.Namespace) -> int:
    """Run ``chimera weasel --mode rpc`` — stdio JSON-RPC 2.0 server.

    Late-binds to :func:`chimera.weasel.rpc.run_rpc_server` (W2). When the
    module is somehow unavailable the placeholder behaviour from the
    scaffold is preserved so shell pipelines surface a clear error.

    Args:
        args: Parsed weasel namespace; ``model`` / ``workdir`` /
            ``max_steps`` are forwarded to the server when present.

    Returns:
        Exit code from the RPC run loop.
    """
    try:
        from chimera.weasel.rpc import run_rpc_server
    except ImportError:
        print(
            "weasel rpc: stdio JSON-RPC mode not yet wired in this scaffold "
            "(see research/weasel/SPEC.md, agent W2).",
            file=sys.stderr,
        )
        return 2
    return int(run_rpc_server(args))


def _run_sdk_mode(_args: argparse.Namespace) -> int:
    """Pointer for ``chimera weasel --mode sdk``.

    The SDK is an import surface, not a CLI mode — invoking it from the
    shell prints the embedding hint and exits. W4 lands the actual
    :class:`chimera.weasel.sdk.Agent`; until then the import path is
    documented but not constructible.
    """
    print(
        "weasel sdk: embed via 'from chimera.weasel.sdk import Agent' "
        "(see research/weasel/SPEC.md, agent W4).",
        file=sys.stderr,
    )
    return 0


# ---------------------------------------------------------------------------
# Subcommand dispatch
# ---------------------------------------------------------------------------


def _dispatch_sessions(args: argparse.Namespace) -> int:
    """Forward ``chimera weasel sessions [list|show <id>|cost]`` to W1's handler.

    Mirrors otter's dispatch shape so the same on-disk eventlog layout
    (under ``~/.chimera/eventlog/weasel-*``) is consumable with the same
    UX as ``chimera otter sessions``. The ``cost`` action re-uses
    :mod:`chimera.mink.cost` so the rollup schema stays identical across
    all four CLIs.
    """
    from chimera.weasel.sessions import dispatch_sessions

    return dispatch_sessions(args)


def _dispatch_share(args: argparse.Namespace) -> int:
    """Forward ``chimera weasel share <session-id>`` to W1's share handler.

    The W1 parser stores the session id in ``args.sub_action`` (the
    second positional slot). We forward the namespace so
    :func:`chimera.weasel.sessions.dispatch_share` reads
    ``share_sink`` / ``share_format`` flags off the same args object.
    """
    from chimera.weasel.sessions import dispatch_share

    return dispatch_share(args)


_SUBCOMMAND_DISPATCH: dict[str, Any] = {
    "sessions": _dispatch_sessions,
    "share": _dispatch_share,
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@friendly_errors
def run(args: argparse.Namespace) -> int:
    """Entry point invoked by ``chimera weasel``.

    Resolves the requested mode + subcommand:

    * ``--list-models`` — print and exit.
    * ``sessions list|show`` — forward to :mod:`chimera.weasel.sessions`.
    * ``-p PROMPT`` — one-shot print mode (text or JSON).
    * ``--mode rpc`` — stdio JSON-RPC server (W2 placeholder).
    * ``--mode sdk`` — embedding pointer.
    * default — interactive REPL via :func:`chimera.weasel.repl.run`.

    Args:
        args: Parsed namespace from the weasel subparser.

    Returns:
        Process exit code.
    """
    # A10-W11: ``--help-long`` shows standard help + long flag descriptions.
    if getattr(args, "help_long", False):
        from chimera.cli.help_long import print_help_long

        print_help_long(_PARSER, _LONG_HELP)
        return 0

    if getattr(args, "list_models", False):
        return _run_list_models()

    sub = getattr(args, "subcommand", None)
    if sub in _SUBCOMMAND_DISPATCH:
        handler = _SUBCOMMAND_DISPATCH[sub]
        return int(handler(args))

    # ``-p`` always wins over --mode for CLI ergonomics parity with the
    # upstream minimal harness's print mode.
    if getattr(args, "print_mode", None) is not None:
        return _run_print_mode(args)

    mode = getattr(args, "mode", "interactive")
    if mode == "rpc":
        return _run_rpc_mode(args)
    if mode == "sdk":
        return _run_sdk_mode(args)
    if mode == "print":
        # --mode print without -p is a usage error; we don't have a prompt
        # to feed the agent. Surface that explicitly rather than dropping
        # into the interactive REPL by accident.
        print(
            "weasel: --mode print requires -p PROMPT",
            file=sys.stderr,
        )
        return 2

    # Interactive (default).
    from chimera.weasel.repl import run as _repl_run

    return _repl_run(args)


__all__ = [
    "add_arguments",
    "run",
]
