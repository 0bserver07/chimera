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
``~/.little-coder/`` (a filesystem path mentioned in docs) is a fact,
not a brand claim.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

# WHY: stdlib only at import time. The interactive path delegates to
# :mod:`chimera.shrew.repl` which itself lazy-imports providers — so
# ``chimera shrew --help`` / ``--version`` stays cheap even when the
# Anthropic / OpenAI SDKs aren't installed.

_VERSION = "0.5.0"
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
_VALID_SUBCOMMANDS = (None, "sessions", "bench")
_VALID_SUB_ACTIONS = (None, "list", "show", "aider-polyglot", "gaia", "terminal-bench")


def _resolve_version() -> str:
    """Return the shrew scaffold version string for ``--version``.

    Returns:
        ``"0.5.0"`` (the per-CLI release line) — independent of the
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
    parser.add_argument(
        "--version",
        action="version",
        version=f"chimera shrew {_resolve_version()}",
    )
    # WHY: env precedence is --model > $SHREW_MODEL > _DEFAULT_MODEL,
    # mirroring weasel's $WEASEL_MODEL pattern. CI / shells pin a model
    # once while keeping ad-hoc --model overrides cheap.
    parser.add_argument(
        "--model",
        default=os.environ.get("SHREW_MODEL") or _DEFAULT_MODEL,
        help=(
            "Model identifier (default: $SHREW_MODEL or "
            f"{_DEFAULT_MODEL}). Local llama.cpp / Ollama models resolve "
            "through ``chimera.shrew.providers`` (S5); cloud models "
            "(``anthropic/claude-...``, ``openai/gpt-...``) fall through "
            "to ``chimera.providers.factory.create_provider``."
        ),
    )
    # WHY: shrew inherits weasel's four-mode philosophy verbatim.
    parser.add_argument(
        "--mode",
        choices=list(_VALID_MODES),
        default="interactive",
        help=(
            "Operating mode: interactive (REPL, default), print "
            "(one-shot text/JSON), rpc (stdio JSON-RPC), or sdk "
            "(prints embedding pointer and exits)."
        ),
    )
    parser.add_argument(
        "-p",
        "--print",
        dest="print_mode",
        default=None,
        help="One-shot: run a single turn with PROMPT, print, exit.",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=False,
        help=(
            "When paired with -p, emit a single JSON object on stdout "
            "(``{output, success, model}``) instead of plain text."
        ),
    )
    parser.add_argument(
        "--list-models",
        dest="list_models",
        action="store_true",
        default=False,
        help=(
            "List models recognised by ``chimera.providers.cost.PRICING`` "
            "and exit."
        ),
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory (default: current directory).",
    )
    # WHY: 30 instead of 50 — small models don't benefit from long
    # horizons. They lose track, loop on tool selection, and burn
    # context. Capping at 30 is the small-model coding agent default.
    parser.add_argument(
        "--max-steps",
        type=int,
        default=_DEFAULT_MAX_STEPS,
        help=(
            f"Maximum agent steps per turn (default: {_DEFAULT_MAX_STEPS}; "
            "smaller than mink/otter's 50 — small models don't benefit "
            "from long horizons)."
        ),
    )
    # WHY: restricted-by-default tool set — Read/Write/Edit/Bash. Small
    # models pick wrong tools when the menu is large; the upstream
    # small-model agent ships exactly this minimal set. ``--allowed-tools=""``
    # opts back into the full default tool group.
    parser.add_argument(
        "--allowed-tools",
        default=_DEFAULT_ALLOWED_TOOLS,
        help=(
            "Comma-separated tool names to allow (case-insensitive; "
            f"default: {_DEFAULT_ALLOWED_TOOLS}). Pass an empty string "
            "(``--allowed-tools=''``) to allow the full default tool "
            "group."
        ),
    )
    # WHY: shrew exposes ``sessions`` (parity with weasel) and ``bench``
    # (S4 benchmark harness — Aider Polyglot / GAIA / terminal-bench).
    parser.add_argument(
        "subcommand",
        nargs="?",
        default=None,
        choices=list(_VALID_SUBCOMMANDS),
        metavar="SUBCOMMAND",
        help=(
            "Optional: 'sessions' (list/show), 'bench' "
            "(aider-polyglot/gaia/terminal-bench)."
        ),
    )
    parser.add_argument(
        "sub_action",
        nargs="?",
        default=None,
        choices=list(_VALID_SUB_ACTIONS),
        metavar="ACTION",
        help=(
            "With 'sessions': 'list' or 'show <id>'. With 'bench': "
            "'aider-polyglot', 'gaia', or 'terminal-bench'."
        ),
    )
    parser.add_argument(
        "sub_target",
        nargs="?",
        default=None,
        metavar="TARGET",
        help="Session id consumed by 'sessions show'.",
    )
    # WHY (S4): bench-specific flag. Keeps the surface unambiguous
    # against future ``sessions`` filters.
    parser.add_argument(
        "--bench-limit",
        dest="bench_limit",
        type=int,
        default=5,
        help=(
            "With 'bench': max tasks to run (default: 5; pass 0 for "
            "full run)."
        ),
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
# print mode (one-shot) — late-binds to weasel
# ---------------------------------------------------------------------------


def _run_print_mode(args: argparse.Namespace) -> int:
    """Execute a single turn via weasel's print-mode plumbing.

    Late-binds to :func:`chimera.weasel.cli._run_print_mode` so the
    one-shot path inherits weasel's provider creation, prompt
    composition, and JSON / text output. Shrew's small-model defaults
    (model, max-steps, allowed-tools) are already resolved on ``args``
    by :func:`add_arguments` before this function runs.

    Args:
        args: Parsed CLI namespace from :func:`add_arguments`.

    Returns:
        Process exit code (``0`` on agent success, ``1`` otherwise).
    """
    # WHY: weasel's _run_print_mode currently doesn't honor an
    # ``--allowed-tools`` filter — its surface is intentionally minimal.
    # We forward through it for now; once weasel grows the filter we
    # inherit automatically. The default tool set is already the full
    # AGENT_TOOLS group, which is acceptable behaviour for a scaffold.
    from chimera.weasel.cli import _run_print_mode as _weasel_print_mode

    return int(_weasel_print_mode(args))


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


_SUBCOMMAND_DISPATCH: dict[str, Any] = {
    "sessions": _dispatch_sessions,
    "bench": _dispatch_bench,
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


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
