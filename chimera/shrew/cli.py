"""``chimera shrew`` — small-model coding-agent CLI (S1 scaffold).

Shrew is the fifth Chimera coding-agent CLI, **explicitly tuned for small
local models** (Qwen3.5-9B, Qwen3.6-35B-A3B MoE, etc.). It is a thin layer
on top of :mod:`chimera.weasel` — same operating modes, same REPL, same
session schema — but with three key small-model adjustments:

* ``--model`` defaults to ``qwen3.6-35b-a3b`` (a llama.cpp-served local
  model identifier) instead of a cloud frontier model.
* ``--max-steps`` defaults to ``30`` (smaller than mink/otter's ``50``);
  small models don't benefit from long horizons and burn budget on
  reasoning loops if given too many steps.
* ``--allowed-tools`` defaults to a restricted ``Read,Write,Edit,Bash``
  subset — the small-model coding agent posture: a minimal high-leverage
  toolkit that fits comfortably inside a 4-bit quantised model's
  effective context budget.

Everything else — provider chain, session persistence, list/show, JSON
output, RPC mode placeholder — late-binds to weasel so improvements to
the substrate flow through automatically. Tests cover the overrides
without booting a real provider.

Trademark hygiene: never names the upstream brand. The on-disk session
prefix is ``shrew-`` (parallel to ``weasel-``, ``otter-``, ``mink-``);
where docs reference an upstream's filesystem layout (e.g. ``~/.shrew/``),
those mentions are paths, not brand claims.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from chimera.cli.help_long import register_argument
from chimera.errors import friendly_errors

# WHY: stdlib only at import time. The interactive path delegates to
# :mod:`chimera.shrew.repl` which itself lazy-imports providers — so
# ``chimera shrew --help`` / ``--version`` stays cheap even when the
# Anthropic / OpenAI SDKs aren't installed.

_VERSION = "0.7.0"
"""Shrew scaffold version. Independent of the chimera package version
because shrew is a per-CLI release line that mirrors weasel's cadence."""

_DEFAULT_MODEL = "qwen3.6-35b-a3b"
"""Default model when neither ``--model`` nor ``$SHREW_MODEL`` is set.

WHY: shrew is small-model-first. ``qwen3.6-35b-a3b`` is a MoE checkpoint
served by llama.cpp at ``http://127.0.0.1:8888/v1`` (see S5 providers).
Cloud fallbacks (``anthropic/claude-haiku-4-5``, ``openai/gpt-4o-mini``,
etc.) work the same way as weasel's via ``--model vendor/name``.
"""

_DEFAULT_MAX_STEPS = 30
"""Default ``--max-steps``. Smaller than mink/otter's 50 because small
models don't benefit from long horizons — extra steps usually mean
loop-detection regressions, not better answers."""

_DEFAULT_ALLOWED_TOOLS = "Read,Write,Edit,Bash"
"""Default ``--allowed-tools``. Restricts the agent to a minimal
high-leverage toolkit. The small-model coding agent posture: cap surface
area so a 9B / 35B-MoE model doesn't burn context on tool selection."""

_VALID_MODES = ("interactive", "print", "rpc", "sdk")
_VALID_SUBCOMMANDS = (None, "sessions", "bench", "share")
_VALID_SUB_ACTIONS = (
    None,
    "list",
    "show",
    "cost",
    "aider-polyglot",
    "gaia",
    "harbor",
    "terminal-bench",
)

# A10-W11: parser ref + per-flag long-form descriptions for ``--help-long``.
_PARSER: argparse.ArgumentParser | None = None
_LONG_HELP: dict[str, str] = {
    "--model": (
        "Model identifier. Resolution order: --model > $SHREW_MODEL > "
        f"the {_DEFAULT_MODEL} default. Local llama.cpp / Ollama models "
        "resolve through chimera.shrew.providers; cloud models "
        "(anthropic/claude-..., openai/gpt-...) fall through to "
        "chimera.providers.factory.create_provider."
    ),
    "--mode": (
        "Operating mode: 'interactive' (REPL, default), 'print' "
        "(one-shot text/JSON), 'rpc' (stdio JSON-RPC server), 'sdk' "
        "(prints embedding pointer and exits)."
    ),
    "-p / --print": (
        "One-shot print mode: run a single agent turn against PROMPT, "
        "emit the assistant text on stdout, then exit. Pairs with "
        "--json for a machine-readable envelope."
    ),
    "--json": (
        "When paired with -p, emit a single JSON object on stdout "
        "({output, success, model}) instead of plain text."
    ),
    "--list-models": (
        "List models recognised by chimera.providers.cost.PRICING and "
        "exit. Useful for discovering valid identifiers before "
        "passing them via --model."
    ),
    "--cwd": (
        "Working directory for the agent run. Default: process cwd. "
        "Resolved to an absolute path before the env is built."
    ),
    "--max-steps": (
        f"Maximum agent steps per turn (default: {_DEFAULT_MAX_STEPS}). "
        "Smaller than mink/otter's 50 — small models don't benefit "
        "from long horizons and burn budget on tool selection."
    ),
    "--allowed-tools": (
        f"Comma-separated tool name allowlist (default: "
        f"{_DEFAULT_ALLOWED_TOOLS}). Pass --allowed-tools='' to allow "
        "the full default tool group."
    ),
    "--resume": (
        "Resume a persisted shrew run by id (matches "
        "~/.chimera/eventlog/<id>/). The replayed conversation is "
        "prepended to the new turn so the agent has full context."
    ),
    "-c / --continue": (
        "Resume the most-recent shrew run under the current working "
        "directory. Equivalent to --resume <newest-shrew-id-in-cwd>."
    ),
    "subcommand": (
        "Optional positional: 'sessions' (list/show/cost), 'bench' "
        "(aider-polyglot / gaia / harbor / terminal-bench), or "
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
        "Defaults to 'file' (writes ~/.chimera/shares/shrew-<id>.<ext>)."
    ),
    "--share-format": (
        "With 'share': render format. Defaults to 'json' "
        "(round-trips with 'sessions show --json'); 'md' yields a "
        "human-readable transcript."
    ),
    "--bench-limit": (
        "With 'bench': max tasks to run (default: 5; pass 0 for the "
        "full benchmark suite)."
    ),
    "--all-clis": (
        "With 'sessions list': include sessions created by every "
        "Chimera CLI (otter / ferret / weasel / stoat / mink / "
        "badger), not just shrew. Adds an ORIGIN column."
    ),
}


def _resolve_version() -> str:
    """Return the shrew scaffold version string for ``--version``.

    Returns:
        ``"0.7.0"`` (the per-CLI release line) — independent of the
        ``chimera-run`` package version. Mirrors weasel's per-CLI release
        cadence so the two CLIs version-bump in lockstep until shrew
        diverges.
    """
    return _VERSION


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register ``chimera shrew`` flags on ``parser``.

    Mirrors :func:`chimera.weasel.cli.add_arguments` but pins three
    small-model defaults: ``--model``, ``--max-steps``, ``--allowed-tools``.
    Embedders / tests can attach the same flag surface to a parser they
    already own.

    Args:
        parser: An :class:`argparse.ArgumentParser` (typically the shrew
            subparser created by :func:`chimera.cli.main.build_parser`).
    """
    # A10-W11: stash for ``--help-long`` rendering in ``run()``.
    global _PARSER
    _PARSER = parser

    parser.add_argument(
        "--version",
        action="version",
        version=f"chimera shrew {_resolve_version()}",
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

    # WHY: env precedence is --model > $SHREW_MODEL > _DEFAULT_MODEL.
    # W14-9: routed through ``register_argument`` so future verbose
    # ``help=`` strings auto-promote to ``_LONG_HELP`` and stay below the
    # 50-line ceiling on ``chimera shrew --help``.
    register_argument(
        core,
        "--model",
        default=os.environ.get("SHREW_MODEL") or _DEFAULT_MODEL,
        metavar="MODEL",
        long_help=_LONG_HELP,
        help=f"Model id (default: $SHREW_MODEL or {_DEFAULT_MODEL}).",
    )
    # WHY: shrew inherits weasel's four-mode philosophy verbatim.
    register_argument(
        core,
        "--mode",
        choices=list(_VALID_MODES),
        default="interactive",
        metavar="MODE",
        long_help=_LONG_HELP,
        help="interactive | print | rpc | sdk (default: interactive).",
    )
    register_argument(
        core,
        "-p",
        "--print",
        dest="print_mode",
        default=None,
        metavar="PROMPT",
        long_help=_LONG_HELP,
        help="One-shot: run PROMPT, print, exit.",
    )
    register_argument(
        output,
        "--json",
        dest="json_output",
        action="store_true",
        default=False,
        long_help=_LONG_HELP,
        help="With -p: emit JSON envelope instead of text.",
    )
    register_argument(
        output,
        "--list-models",
        dest="list_models",
        action="store_true",
        default=False,
        long_help=_LONG_HELP,
        help="List recognised model ids and exit.",
    )
    register_argument(
        core,
        "--cwd",
        default=None,
        long_help=_LONG_HELP,
        help="Working directory (default: cwd).",
    )
    # WHY: 30 instead of 50 — small models don't benefit from long horizons.
    register_argument(
        behavior,
        "--max-steps",
        type=int,
        default=_DEFAULT_MAX_STEPS,
        metavar="N",
        long_help=_LONG_HELP,
        help=f"Max agent steps per turn (default: {_DEFAULT_MAX_STEPS}).",
    )
    # WHY: restricted-by-default tool set — Read/Write/Edit/Bash.
    register_argument(
        behavior,
        "--allowed-tools",
        default=_DEFAULT_ALLOWED_TOOLS,
        metavar="LIST",
        long_help=_LONG_HELP,
        help=f"Comma allowlist (default: {_DEFAULT_ALLOWED_TOOLS}).",
    )
    # WHY (C1, wave 9): --resume / --continue mirror mink's flag pair.
    register_argument(
        persistence,
        "--resume",
        default=None,
        metavar="ID",
        long_help=_LONG_HELP,
        help="Resume a persisted shrew run by id.",
    )
    register_argument(
        persistence,
        "-c",
        "--continue",
        dest="continue_latest",
        action="store_true",
        default=False,
        long_help=_LONG_HELP,
        help="Resume the newest shrew run under cwd.",
    )
    # WHY: shrew exposes ``sessions`` (parity with weasel) and ``bench``.
    register_argument(
        parser,
        "subcommand",
        nargs="?",
        default=None,
        choices=list(_VALID_SUBCOMMANDS),
        metavar="SUBCOMMAND",
        long_help=_LONG_HELP,
        help="sessions | bench | share.",
    )
    parser.add_argument(
        "sub_action",
        nargs="?",
        default=None,
        metavar="ACTION",
        help="list | show | cost | <suite> | <session-id>.",
    )
    parser.add_argument(
        "sub_target",
        nargs="?",
        default=None,
        metavar="TARGET",
        help="Session id for sessions show.",
    )
    # WHY: cost subcommand flags. Mirror ``mink runs cost``.
    register_argument(
        persistence,
        "--since",
        dest="cost_since",
        default=None,
        metavar="WINDOW",
        long_help=_LONG_HELP,
        help="sessions cost: cutoff (e.g. 7d / ISO).",
    )
    register_argument(
        persistence,
        "--cost-model",
        dest="cost_model",
        default=None,
        metavar="STR",
        long_help=_LONG_HELP,
        help="sessions cost: model substring filter.",
    )
    register_argument(
        persistence,
        "--cost-format",
        dest="cost_format",
        choices=("text", "json", "csv"),
        default=None,
        metavar="FMT",
        long_help=_LONG_HELP,
        help="sessions cost: text | json | csv.",
    )
    register_argument(
        persistence,
        "--cost-limit",
        dest="cost_limit",
        type=int,
        default=None,
        metavar="N",
        long_help=_LONG_HELP,
        help="sessions cost: row cap (newest first).",
    )
    # WHY: share subcommand flags. Mirror weasel's share surface.
    register_argument(
        persistence,
        "--share-sink",
        dest="share_sink",
        choices=("file", "stdout"),
        default=None,
        metavar="SINK",
        long_help=_LONG_HELP,
        help="share: file (default) | stdout.",
    )
    register_argument(
        persistence,
        "--share-format",
        dest="share_format",
        choices=("json", "md"),
        default=None,
        metavar="FMT",
        long_help=_LONG_HELP,
        help="share: json (default) | md.",
    )
    # WHY (S4): bench-specific flag.
    register_argument(
        behavior,
        "--bench-limit",
        dest="bench_limit",
        type=int,
        default=5,
        metavar="N",
        long_help=_LONG_HELP,
        help="bench: max tasks (default: 5; 0 = full run).",
    )
    # B9-W11: cross-CLI session listing.
    register_argument(
        persistence,
        "--all-clis",
        dest="sessions_all_clis",
        action="store_true",
        default=False,
        long_help=_LONG_HELP,
        help="sessions list: include every Chimera CLI's sessions.",
    )


# ---------------------------------------------------------------------------
# Extension wiring (S3) — small-model-fit helpers
# ---------------------------------------------------------------------------
#
# WHY: shrew layers three small-model adjustments on top of weasel —
# MoE-aware context sizing, scaffold-fit prompt wrapping, and tool-list
# trimming for tiny models. Each lives in :mod:`chimera.shrew.extensions`
# and is consumed via a thin adapter so the CLI module stays cheap to
# import (stdlib-only at top-level; the extensions are likewise stdlib-only,
# so the lazy imports here are belt-and-suspenders).


def _resolve_vram_gb(args: argparse.Namespace) -> int:
    """Resolve a VRAM budget for MoE context sizing.

    Lookup order:
      1. ``args.vram_gb`` (when S3 grows the flag);
      2. ``$SHREW_VRAM_GB`` (explicit override);
      3. fallback to 8 (the laptop-class default the upstream
         small-coder project targets).
    """
    explicit = getattr(args, "vram_gb", None)
    if isinstance(explicit, int) and explicit > 0:
        return explicit
    env_val = os.environ.get("SHREW_VRAM_GB", "").strip()
    if env_val:
        try:
            parsed = int(env_val)
        except ValueError:
            parsed = 0
        if parsed > 0:
            return parsed
    return 8


def apply_small_model_extensions(
    args: argparse.Namespace,
    *,
    system_prompt: str | None = None,
    tools: list[Any] | None = None,
) -> dict[str, Any]:
    """Apply the three S3 small-model extensions and return their outputs.

    Late-binding wrapper around :mod:`chimera.shrew.extensions`. Callers
    (the REPL, RPC mode, print mode, and tests) feed in whatever they
    have on hand — a system prompt, a tool list, or just ``args`` for
    context-window sizing — and we return a dict with the adapted
    values. Inputs are never mutated.

    Args:
        args: The parsed shrew namespace; consulted for ``model``
            and ``vram_gb`` (when present).
        system_prompt: Optional system prompt to wrap. ``None`` skips.
        tools: Optional tool list to filter. ``None`` skips.

    Returns:
        Mapping with keys:
          * ``"model"`` — the model id from ``args``;
          * ``"context_window"`` — recommended ``-c`` value;
          * ``"system_prompt"`` — wrapped (or unchanged) prompt, or
            ``None`` if not provided;
          * ``"tools"`` — filtered (or unchanged) tool list, or
            ``None`` if not provided;
          * ``"model_size_b"`` — best-effort param count (or ``None``).
    """
    from chimera.shrew.extensions import (
        compute_optimal_context_window,
        filter_tools_for_model,
        model_size_billions,
        wrap_for_small_model,
    )

    model_id = getattr(args, "model", _DEFAULT_MODEL) or _DEFAULT_MODEL
    vram_gb = _resolve_vram_gb(args)
    context_window = compute_optimal_context_window(model_id, vram_gb)
    size_b = model_size_billions(model_id)

    out: dict[str, Any] = {
        "model": model_id,
        "context_window": context_window,
        "model_size_b": size_b,
        "system_prompt": None,
        "tools": None,
    }
    if system_prompt is not None:
        # Use active-params count for MoE so qwen3.6-35b-a3b (3B
        # active) gets the small-model scaffold even though its
        # nominal label is 35B. Unknown size → fail-open (don't
        # penalise frontier models with small-model scaffolding).
        if size_b is None:
            out["system_prompt"] = system_prompt
        else:
            out["system_prompt"] = wrap_for_small_model(
                system_prompt, size_b,
            )
    if tools is not None:
        out["tools"] = filter_tools_for_model(tools, model_id)
    return out


# ---------------------------------------------------------------------------
# --list-models
# ---------------------------------------------------------------------------


def _run_list_models() -> int:
    """Print known model identifiers (from ``chimera.providers.cost.PRICING``).

    Returns:
        Process exit code (always ``0`` on success, ``1`` on import drift).
    """
    try:
        from chimera.providers.cost import PRICING
    except Exception as exc:  # noqa: BLE001 — never crash on import drift
        print(f"shrew: could not load model registry: {exc}", file=sys.stderr)
        return 1
    for model in sorted(PRICING):
        print(model)
    return 0


# ---------------------------------------------------------------------------
# print mode (one-shot) — shrew-native (skills + extensions wired)
# ---------------------------------------------------------------------------


_BASE_SYSTEM_PROMPT = (
    "You are Shrew, a small-model coding agent. "
    "Use tools to inspect and modify the user's repo. "
    "Be concise; one tool call per turn."
)
"""Base system prompt for shrew print/REPL paths.

Kept short on purpose — small models do worse with verbose system
prompts. The S3 :func:`wrap_for_small_model` scaffold layers
explicit step-by-step reasoning rules around it when the active
model is below :data:`SMALL_MODEL_THRESHOLD_B`."""


# Friendly alias map for ``--allowed-tools``. Lets users write the
# capitalised, terse names the upstream small-model coding agent uses
# (``Read,Write,Edit,Bash``) while still matching Chimera's snake-case
# tool registry (``read_file``, ``write_file``, ``edit_file``, ``bash``).
# All comparisons are lowercase; aliases listed here are *also* matched
# against the canonical name so explicit ``read_file`` keeps working.
_TOOL_NAME_ALIASES: dict[str, set[str]] = {
    "read_file": {"read", "read_file", "readfile"},
    "write_file": {"write", "write_file", "writefile"},
    "edit_file": {"edit", "edit_file", "editfile"},
    "bash": {"bash", "shell"},
    "search": {"search", "grep"},
    "list_files": {"list", "list_files", "ls"},
    "test": {"test"},
    "git": {"git"},
    "replace_in_file": {"replace", "replace_in_file"},
    "read_image": {"image", "read_image", "image_read"},
    "repo_map": {"repo_map", "repomap"},
    "think": {"think"},
    "todo": {"todo"},
    "verify_answer": {"verify", "verify_answer"},
    "web_search": {"web_search", "web", "websearch"},
}


def _matches_allowed(tool_name: str, wanted: set[str]) -> bool:
    """Return True when ``tool_name`` (canonical) is in ``wanted``.

    Honours :data:`_TOOL_NAME_ALIASES` so ``"Read"`` matches
    ``"read_file"``.
    """
    canonical = tool_name.lower()
    if canonical in wanted:
        return True
    aliases = _TOOL_NAME_ALIASES.get(canonical, set())
    return bool(aliases & wanted)


def _filter_tools_by_allowed(tools: list[Any], allowed: str | None) -> list[Any]:
    """Filter ``tools`` down to the ``allowed`` comma-separated names.

    Mirrors the small-model coding agent posture: cap surface area so
    a 9B / 35B-MoE model doesn't burn context on tool selection.
    Comparison is case-insensitive and honours
    :data:`_TOOL_NAME_ALIASES` so ``Read,Write,Edit,Bash`` resolves to
    the canonical ``read_file,write_file,edit_file,bash`` set.
    Empty / ``None`` ``allowed`` opts back into the full input list.

    Args:
        tools: Tools to filter (typically :data:`AGENT_TOOLS`).
        allowed: Comma-separated tool-name allow-list, or ``""``/``None``.

    Returns:
        New list of tools whose ``.name`` matches ``allowed`` (or the
        input list when ``allowed`` is empty).
    """
    if not allowed:
        return list(tools)
    wanted = {n.strip().lower() for n in allowed.split(",") if n.strip()}
    if not wanted:
        return list(tools)
    out: list[Any] = []
    for t in tools:
        name = str(getattr(t, "name", ""))
        if _matches_allowed(name, wanted):
            out.append(t)
    return out


def _run_print_mode(args: argparse.Namespace) -> int:
    """Execute a single turn with shrew's small-model defaults applied.

    Builds the provider via :func:`chimera.shrew.providers.build_provider`
    (llama.cpp first, Ollama next, cloud fallback) so a colon-tagged
    Ollama id like ``glm-5.1:cloud`` routes correctly without weasel's
    OpenAI-first chain getting in the way. Mounts the bundled S2 skills
    into the system prompt, applies the S3 scaffold-fit wrap, filters
    the tool list by ``--allowed-tools`` and then by S3
    :func:`filter_tools_for_model`, and sets the provider's effective
    context window from :func:`compute_optimal_context_window`.

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
    from chimera.shrew.providers import build_provider as _shrew_build_provider
    from chimera.shrew.skills import (
        discover_shrew_skills,
        format_shrew_skills_for_prompt,
    )

    cwd = os.path.abspath(getattr(args, "cwd", None) or os.getcwd())

    # --- Provider (small-model-first chain) ---
    try:
        provider = _shrew_build_provider(args)
    except Exception as exc:  # noqa: BLE001 — surface provider auth errors cleanly
        print(f"shrew: provider error: {exc}", file=sys.stderr)
        return 1

    # --- Skills (S2): mount the 11 bundled skill summaries into the prompt ---
    skills_block = ""
    skill_count = 0
    try:
        from pathlib import Path as _Path

        skills = discover_shrew_skills(
            extra_search_paths=[_Path.home() / ".shrew" / "skills"],
        )
        skill_count = len(skills)
        skills_block = format_shrew_skills_for_prompt(skills)
    except Exception as exc:  # noqa: BLE001 — never crash on skill discovery drift
        print(f"shrew: skill discovery skipped: {exc}", file=sys.stderr)

    composed_prompt = _BASE_SYSTEM_PROMPT
    if skills_block:
        composed_prompt = f"{_BASE_SYSTEM_PROMPT}\n\n{skills_block}"

    # --- Tools: --allowed-tools filter, then S3 tiny-model trim ---
    full_tools = list(AGENT_TOOLS)
    allowed_tools = getattr(args, "allowed_tools", _DEFAULT_ALLOWED_TOOLS)
    base_tools = _filter_tools_by_allowed(full_tools, allowed_tools)
    pre_filter_tool_count = len(base_tools)

    # --- Extensions (S3): scaffold-fit prompt + tool filter + context window ---
    ext = apply_small_model_extensions(
        args,
        system_prompt=composed_prompt,
        tools=base_tools,
    )
    final_prompt = ext["system_prompt"] or composed_prompt
    final_tools = ext["tools"] if ext["tools"] is not None else base_tools
    context_window = int(ext["context_window"])
    model_size_b = ext["model_size_b"]
    scaffold_applied = (
        model_size_b is not None
        and final_prompt != composed_prompt
    )
    tools_dropped = pre_filter_tool_count - len(final_tools)

    # --- Override provider context window when the extension recommends one ---
    # The OpenAI-compatible provider exposes ``_context_length`` (and the
    # public ``context_window`` property reads from it). Setting this
    # value drives compaction triggering at the loop level via
    # ``LoopConfig.auto_compact_threshold`` * provider.context_window.
    if hasattr(provider, "_context_length"):
        try:
            provider._context_length = context_window  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — never crash on provider drift
            pass

    # --- Stderr breadcrumbs (so live runs can verify wiring) ---
    breadcrumbs = (
        f"shrew: skills={skill_count} mounted; "
        f"scaffold={'on' if scaffold_applied else 'off'}; "
        f"tools={len(final_tools)} (dropped {tools_dropped}); "
        f"context_window={context_window}; "
        f"model={getattr(provider, 'model_name', getattr(args, 'model', '?'))}; "
        f"size_b={model_size_b}"
    )
    print(breadcrumbs, file=sys.stderr)

    # --- Agent + loop ---
    env = LocalEnvironment(workdir=cwd)
    env.setup()

    cancel = CancellationToken()
    # WHY: ``-p`` is headless — there is no human present to answer
    # ASK prompts. ``LoopConfig`` defaults to an :class:`Interactive`
    # policy that ASKs for ``bash`` / ``write_file`` / ``edit_file``,
    # which :func:`async_drain_steps` then auto-denies. The CLI has
    # *already* fenced the tool surface via ``--allowed-tools`` (the
    # default itself is the minimal ``Read,Write,Edit,Bash`` set), so
    # the LoopConfig-level ASK is redundant and actively harmful: it
    # turns every bash call in print mode into ``Auto-denied by
    # async_drain_steps`` and pushes small models into max-step loops.
    # Explicitly opt the print path into ``AutoApprove`` so the
    # ``--allowed-tools`` filter remains the sole gate.
    from chimera.permissions.presets import AutoApprove
    config = LoopConfig(cancellation=cancel, permissions=AutoApprove())
    loop = ReAct(max_steps=int(getattr(args, "max_steps", _DEFAULT_MAX_STEPS)), config=config)
    prompt = Prompt.from_string(final_prompt)
    agent = Agent(
        provider=provider,
        tools=final_tools,
        loop=loop,
        prompt=prompt,
    )

    # WHY (C1, wave 9): apply ``--resume`` / ``-c`` before dispatching to
    # the agent so a one-shot run can pick up the prior shrew context.
    effective_prompt = _apply_shrew_resume_prefix(
        args, default_prompt=args.print_mode,
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


def _apply_shrew_resume_prefix(
    args: argparse.Namespace,
    *,
    default_prompt: str,
) -> str:
    """Resolve ``--resume`` / ``--continue`` for shrew.

    Symmetric helper to otter / ferret / weasel's resume-prefix wrappers.
    Prefix is hard-coded to ``shrew-``.

    Args:
        args: The parsed shrew argparse namespace.
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
        prefix="shrew-",
        eventlog_root=default_eventlog_root(),
        cwd=os.path.abspath(getattr(args, "cwd", None) or os.getcwd()),
    )
    if target_id is None:
        return default_prompt

    try:
        session = resume_run(target_id)
    except (ValueError, OSError) as exc:
        print(
            f"[shrew] --resume / --continue: failed to load run "
            f"{target_id!r}: {exc}",
            file=sys.stderr,
        )
        return default_prompt

    messages = list(getattr(session, "messages", []) or [])
    if not messages:
        return default_prompt

    sys.stderr.write(
        f"[shrew] resumed run {target_id} ({len(messages)} messages)\n"
    )
    sys.stderr.flush()
    transcript = build_resume_prefix(messages)
    return f"{transcript}{default_prompt}"


# ---------------------------------------------------------------------------
# RPC + SDK placeholders
# ---------------------------------------------------------------------------


def _run_rpc_mode(args: argparse.Namespace) -> int:
    """Run ``chimera shrew --mode rpc`` — late-binds to weasel's RPC server.

    Shrew has no RPC-specific behaviour today; the upstream weasel RPC
    server already accepts every shrew namespace attribute it needs.
    When the weasel RPC module is unavailable we surface a clear stderr
    error rather than silently no-op'ing.
    """
    try:
        from chimera.weasel.rpc import run_rpc_server
    except ImportError:
        print(
            "shrew rpc: stdio JSON-RPC mode unavailable "
            "(weasel RPC dependency missing).",
            file=sys.stderr,
        )
        return 2
    return int(run_rpc_server(args))


def _run_sdk_mode(_args: argparse.Namespace) -> int:
    """Pointer for ``chimera shrew --mode sdk``.

    The SDK is an import surface, not a CLI mode. Until shrew ships
    its own SDK module, point users at weasel's embeddable Agent.
    """
    print(
        "shrew sdk: embed via 'from chimera.weasel.sdk import Agent' "
        "(shrew shares weasel's SDK; small-model defaults are CLI-only).",
        file=sys.stderr,
    )
    return 0


# ---------------------------------------------------------------------------
# Subcommand dispatch
# ---------------------------------------------------------------------------


def _dispatch_sessions(args: argparse.Namespace) -> int:
    """Forward ``chimera shrew sessions [list|show <id>]`` to S1's handler.

    Shrew's sessions live under ``~/.chimera/eventlog/shrew-*`` (parallel
    to ``weasel-*``). The handler is owned by :mod:`chimera.shrew.sessions`.
    """
    from chimera.shrew.sessions import dispatch_sessions

    return dispatch_sessions(args)


def _dispatch_bench(args: argparse.Namespace) -> int:
    """Late-bind ``chimera shrew bench`` to :mod:`chimera.shrew.benchmarks`.

    Owned by S4 (Aider Polyglot / GAIA / terminal-bench adapters). Until
    the module lands, surface a clear stderr message and return 2.
    """
    try:
        from chimera.shrew.benchmarks.cli import dispatch_bench  # type: ignore[import-not-found]
    except ImportError:
        action = getattr(args, "sub_action", None) or "(missing)"
        print(
            f"shrew bench {action}: benchmark harness not yet wired in this "
            "scaffold (see research/shrew/SPEC.md, agent S4).",
            file=sys.stderr,
        )
        return 2
    return int(dispatch_bench(args))


def _dispatch_share(args: argparse.Namespace) -> int:
    """Forward ``chimera shrew share <session-id>`` to S1's share handler.

    The S1 parser stores the session id in ``args.sub_action`` (the
    second positional slot). The namespace is forwarded so
    :func:`chimera.shrew.sessions.dispatch_share` reads
    ``share_sink`` / ``share_format`` flags off the same args object.
    """
    from chimera.shrew.sessions import dispatch_share

    return dispatch_share(args)


_SUBCOMMAND_DISPATCH: dict[str, Any] = {
    "sessions": _dispatch_sessions,
    "bench": _dispatch_bench,
    "share": _dispatch_share,
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@friendly_errors
def run(args: argparse.Namespace) -> int:
    """Entry point invoked by ``chimera shrew``.

    Resolves the requested mode + subcommand:

    * ``--list-models`` — print and exit.
    * ``sessions list|show`` — forward to :mod:`chimera.shrew.sessions`.
    * ``bench <name>`` — forward to :mod:`chimera.shrew.benchmarks`.
    * ``-p PROMPT`` — one-shot print mode (text or JSON, via weasel).
    * ``--mode rpc`` — stdio JSON-RPC server (via weasel).
    * ``--mode sdk`` — embedding pointer.
    * default — interactive REPL via :func:`chimera.shrew.repl.run`.

    Args:
        args: Parsed namespace from the shrew subparser.

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

    # ``-p`` always wins over --mode for CLI ergonomics parity with
    # weasel and the upstream small-model coding agent's print mode.
    if getattr(args, "print_mode", None) is not None:
        return _run_print_mode(args)

    mode = getattr(args, "mode", "interactive")
    if mode == "rpc":
        return _run_rpc_mode(args)
    if mode == "sdk":
        return _run_sdk_mode(args)
    if mode == "print":
        # --mode print without -p is a usage error; we don't have a
        # prompt to feed the agent. Surface that explicitly rather than
        # dropping into the interactive REPL by accident.
        print(
            "shrew: --mode print requires -p PROMPT",
            file=sys.stderr,
        )
        return 2

    # Interactive (default).
    from chimera.shrew.repl import run as _repl_run

    return _repl_run(args)


__all__ = [
    "add_arguments",
    "apply_small_model_extensions",
    "run",
]
