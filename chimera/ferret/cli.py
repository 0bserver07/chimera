"""``chimera ferret`` — Ferret, a Chimera coding agent in the IDE-first tradition.

Ferret is the third Chimera coding-agent CLI, paralleling :mod:`chimera.mink`
and :mod:`chimera.otter`. Where mink mirrors a TUI-first ergonomic and otter
mirrors a server-first / multi-client posture, ferret mirrors a sandbox-first /
IDE-first / OpenAI-flagship coding agent (the upstream reference).

This module ships the **scaffold**: a working ``add_arguments`` / ``run``
pair so ``chimera ferret --version`` and ``chimera ferret -p "..."`` route
through. Subcommand placeholders (``serve`` / ``sessions`` / ``share`` /
``agents`` / ``bench``) are recognised and dispatched to stub handlers;
sibling agents in the wave fill in the bodies (sandbox, approval, IDE, cloud
bridge, providers).

Conventions follow ``chimera/otter/cli.py`` closely so users moving between
``chimera otter`` and ``chimera ferret`` pay no surprise tax.

Trademark hygiene: this module never names the upstream IDE-first OpenAI-
flagship coding agent in source/docs/help text. ``~/.codex/config.toml`` is
referenced as a filesystem path (a fact, not a brand claim).
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from chimera.cli.help_long import register_argument
from chimera.errors import friendly_errors

# WHY: only stdlib + chimera at import time. Provider deps (httpx, anthropic,
# openai SDKs) are pulled in lazily inside ``_build_provider`` so importing
# ``chimera.ferret.cli`` for ``--help`` / ``--version`` stays cheap. The
# sandbox / approval / IDE / cloud-bridge / providers siblings are similarly
# late-bound so this file loads even when the rest of the wave hasn't landed.

_DEFAULT_MODEL = "gpt-5"
"""Default model when neither ``--model`` nor ``$FERRET_MODEL`` is set.

WHY: the upstream IDE-first OpenAI-flagship coding agent's provider chain
prefers OpenAI's flagship model. We name it ``gpt-5`` per the spec's
provider chain (see ``research/ferret/SPEC.md``). When the OpenAI SDK
isn't configured, the downstream :mod:`chimera.ferret.providers` resolver
(FF6) falls through to Anthropic / OpenRouter; until that lands, the
provider factory raises a friendly "no API key" error.
"""

_VALID_OUTPUT_FORMATS = ("text", "json", "stream-json")
_VALID_SUBCOMMANDS = (
    None,
    "serve",
    "sessions",
    "share",
    "agents",
    "bench",
    "bridge",
)
_VALID_SUB_ACTIONS = (
    None,
    "list",
    "show",
    "humaneval",
    "tau-bench",
    # WHY (server-mgmt): ``serve status`` / ``serve stop`` reuse the
    # ``sub_action`` slot; declared here so argparse choices validation
    # accepts them across every ferret subcommand.
    "status",
    "stop",
)
_VALID_SANDBOX_MODES = (
    "read-only",
    "workspace-write",
    "workspace-write-network",
)
_VALID_APPROVAL_PRESETS = ("read-only", "auto", "full")
# WHY (G3, w13): the cross-CLI ``--permission-mode`` 5-mode surface.
# ``read-only`` / ``suggest`` / ``auto`` / ``yolo`` / ``strict`` mirrors
# the spelling other coding-agent CLIs ship without naming them. Maps
# onto :class:`chimera.permissions.modes.ApprovalMode` and selects a
# preset PermissionPolicy via ``policy_for_mode``. Coexists with the
# legacy ``--approval`` flag (3 presets); ``--permission-mode`` wins
# when both are explicitly set.
_VALID_PERMISSION_MODES = ("read-only", "suggest", "auto", "yolo", "strict")
_VALID_OS_SANDBOX_FLAGS = ("auto", "on", "off")
# WHY (P1, wave 9): pluggable execution backend for ferret tool calls.
# ``local`` is the historic default (LocalEnvironment + ferret sandbox
# wrapper). ``modal`` provisions an ephemeral Modal container per
# session via :class:`chimera.env.modal_sandbox.ModalSandboxEnvironment`.
# Adding a backend here is a one-liner: extend the tuple, then teach
# ``_run_print_mode`` how to construct it.
_VALID_SANDBOX_BACKENDS = ("local", "modal")

# A10-W11: parser ref + per-flag long descriptions for ``--help-long``.
_PARSER: argparse.ArgumentParser | None = None
_LONG_HELP: dict[str, str] = {
    "--model": (
        "Model identifier. Resolution order: --model > $FERRET_MODEL > "
        f"the {_DEFAULT_MODEL} default. Routed through "
        "chimera.ferret.providers.build_provider with fallback to "
        "chimera.providers.factory.create_provider."
    ),
    "-p / --print": (
        "One-shot print mode: run a single agent turn against PROMPT, "
        "emit the assistant text on stdout, then exit. Pairs with "
        "--output-format json for a structured envelope."
    ),
    "--output-format": (
        "One-shot output format. 'text' (default) prints the assistant "
        "reply; 'json' emits a single result object on exit; "
        "'stream-json' prints one JSON line per LoopEvent."
    ),
    "--max-steps": "Maximum agent steps per turn (default: 50).",
    "--cwd": (
        "Working directory for the agent run. Default: process cwd. "
        "Resolved to an absolute path before the env is built."
    ),
    "--allowed-tools": (
        "Comma-separated tool names to allow (case-insensitive). "
        "Empty means every tool in AGENT_TOOLS is exposed."
    ),
    "--no-rich": (
        "Force the plain ConsoleStreamHandler even when stdout is a "
        "TTY. Default: auto-select rich on TTY, plain when piped."
    ),
    "--no-color": (
        "Synonym for --no-rich. Also honored implicitly when the "
        "$NO_COLOR environment variable is set."
    ),
    "--no-save": (
        "Do not persist the one-shot run to ~/.chimera/eventlog/. "
        "Default behavior saves the full message + tool history."
    ),
    "--run-id": (
        "Override the auto-generated run id for the persisted "
        "eventlog directory. Useful for reproducible test fixtures."
    ),
    "--resume": (
        "Resume a persisted ferret run by id (matches "
        "~/.chimera/eventlog/<id>/). The replayed conversation is "
        "prepended to the new turn so the agent has full context."
    ),
    "-c / --continue": (
        "Resume the most-recent ferret run under the current working "
        "directory. Equivalent to --resume <newest-ferret-id-in-cwd>."
    ),
    "--sandbox": (
        "Sandbox mode for shell-style tools (default: read-only). "
        "'workspace-write' allows writes inside the project; "
        "'workspace-write-network' adds outbound network access."
    ),
    "--os-sandbox": (
        "OS-level sandbox layer for shell tools (default: auto). "
        "'auto' engages seatbelt (macOS) or Landlock (Linux) if "
        "supported; 'on' forces it; 'off' disables it."
    ),
    "--sandbox-backend": (
        "Execution backend for tool calls (default: local). 'local' "
        "runs inside the current cwd via LocalEnvironment. 'modal' "
        "provisions an ephemeral Modal container per session "
        "(requires `pip install 'chimera-run[modal-sandbox]'`)."
    ),
    "--approval": (
        "Approval preset (default: read-only). 'auto' approves "
        "low-risk tools and prompts for high-risk; 'full' approves "
        "all tool calls without prompting. Legacy 3-preset surface; "
        "see --permission-mode for the 5-mode standard."
    ),
    "--permission-mode": (
        "5-mode approval surface (cross-CLI standard). 'read-only' "
        "denies all writes; 'suggest' allows reads and asks for "
        "writes; 'auto' allows reads + edits and asks for shell; "
        "'yolo' approves everything; 'strict' asks for every tool "
        "call. Wins over --approval when both are passed."
    ),
    "--config": (
        "Override the ferret config file path. Default: merge "
        "~/.codex/config.toml with project ./.codex/config.toml."
    ),
    "--http": (
        "With 'serve': run the HTTP server instead of the default ACP "
        "(Agent Client Protocol) JSON-RPC server on stdio."
    ),
    "--host": (
        "With 'serve --http': bind host (default: 127.0.0.1). "
        "Use 0.0.0.0 only with --auth-token."
    ),
    "--port": "With 'serve --http': bind port (default: 5174).",
    "--auth-token": (
        "With 'serve --http': shared-secret bearer token required on "
        "every request except /healthz."
    ),
    "--tls-cert": (
        "With 'serve --http': path to a PEM-encoded server "
        "certificate. Must be paired with --tls-key. When set the "
        "server speaks HTTPS."
    ),
    "--tls-key": (
        "With 'serve --http': path to a PEM-encoded private key "
        "matching --tls-cert."
    ),
    "--remote-url": (
        "With 'bridge': HTTPS base URL of the remote bridge service. "
        "Default points at a placeholder .invalid domain — operators "
        "must opt in to a real remote."
    ),
    "--bridge-token": (
        "With 'bridge': shared-secret bearer token sent on every "
        "request. Falls back to $FERRET_BRIDGE_TOKEN."
    ),
    "subcommand": (
        "Optional positional: 'serve' (ACP/HTTP server), 'sessions' "
        "(list/show), 'share' (export a session), 'agents' (list/"
        "show), 'bench' (benchmark suites), 'bridge' (cloud bridge)."
    ),
    "--all": (
        "With 'serve stop': stop every backgrounded ferret server. "
        "Mutually exclusive with --port."
    ),
    "--serve-timeout": (
        "With 'serve stop': seconds to wait after SIGTERM before "
        "escalating (default: 10.0)."
    ),
    "--all-clis": (
        "With 'sessions list': include sessions created by every "
        "Chimera CLI (otter / weasel / shrew / stoat / mink / "
        "badger), not just ferret. Adds an ORIGIN column."
    ),
    "--full-auto": (
        "Shortcut for '--approval auto': low-risk tools auto-approve, "
        "high-risk prompts. Loses to an explicit --permission-mode / "
        "--approval; loses to --yolo when both are passed."
    ),
    "--yolo": (
        "Shortcut for '--approval yolo'. DANGEROUS: every tool call "
        "auto-approves, including shell + writes outside the project. "
        "Prints a stderr warning on every invocation. Wins over "
        "--full-auto and --approval; loses only to an explicit "
        "--permission-mode."
    ),
    "--add-dir": (
        "Add an extra writable directory beyond the project cwd. "
        "Repeatable. Surfaces on args.add_dirs as a list and is "
        "available to sandbox/permission resolvers that opt in."
    ),
    "--skip-git-repo-check": (
        "Bypass the guard that refuses to start ferret outside a git "
        "repository. Useful for one-off scripts or when running on a "
        "fresh checkout that hasn't been 'git init'd yet."
    ),
    "--image": (
        "Attach an image file to the prompt. Repeatable. Each image "
        "path is rendered into the user message as an annotation; the "
        "agent can fetch the bytes via the read_image tool."
    ),
    "--profile": (
        "Load a TOML profile from ~/.chimera/profiles/<NAME>.toml and "
        "overlay its keys onto the parsed args before resolution. "
        "Recognised keys mirror argparse dest names (model, sandbox, "
        "approval, permission_mode, max_steps, allowed_tools, "
        "add_dirs, images, skip_git_repo_check)."
    ),
}


def _resolve_version() -> str:
    """Resolve the chimera package version for ``--version`` output.

    Mirrors :func:`chimera.otter.cli._resolve_version` so otter and ferret
    print the same semver under the same install.

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
    """Register ``chimera ferret`` flags on ``parser``.

    Mirrors otter's ``add_arguments`` shape so embedders / tests can attach
    the same flag surface to a parser they already own. Adds ferret-only
    flags ``--sandbox`` and ``--approval`` (FF2 + FF3) and the ferret
    config knob ``--config`` (FF1) on top of the shared otter set.

    Args:
        parser: An :class:`argparse.ArgumentParser` (typically the ferret
            subparser created by :func:`chimera.cli.main.build_parser`).
    """
    # A10-W11: stash for ``--help-long`` rendering in ``run()``.
    global _PARSER
    _PARSER = parser
    # A10-W11: short usage line keeps ``--help`` <=50 lines. The full
    # auto-generated usage is still available via ``--help-long``.
    parser.usage = (
        "chimera ferret [OPTIONS] [SUBCOMMAND] [ACTION] [TARGET]"
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"chimera ferret {_resolve_version()}",
        help=argparse.SUPPRESS,
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
    serve_grp = parser.add_argument_group("Serve / Bridge")

    # WHY: env precedence is --model > $FERRET_MODEL > _DEFAULT_MODEL.
    # ``register_argument`` keeps the help-screen short while the full
    # provider chain detail lives in ``_LONG_HELP["--model"]``.
    register_argument(
        core,
        "--model",
        default=os.environ.get("FERRET_MODEL") or _DEFAULT_MODEL,
        metavar="MODEL",
        long_help=_LONG_HELP,
        help_short=f"Model id (env: $FERRET_MODEL; default {_DEFAULT_MODEL}).",
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
        "--output-format",
        choices=list(_VALID_OUTPUT_FORMATS),
        default="text",
        metavar="FMT",
        help="text | json | stream-json (default: text).",
    )
    behavior.add_argument(
        "--max-steps",
        type=int,
        default=50,
        metavar="N",
        help="Max agent steps per turn (default: 50).",
    )
    core.add_argument(
        "--cwd",
        default=None,
        help="Working directory (default: cwd).",
    )
    behavior.add_argument(
        "--allowed-tools",
        default="",
        metavar="LIST",
        help="Comma tool allowlist (empty = all).",
    )
    output.add_argument(
        "--no-rich",
        action="store_true",
        default=False,
        help="Force plain stream handler even on TTY.",
    )
    output.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        help="Synonym for --no-rich (also honors $NO_COLOR).",
    )
    persistence.add_argument(
        "--no-save",
        action="store_true",
        default=False,
        help="Don't persist the one-shot run to eventlog.",
    )
    persistence.add_argument(
        "--run-id",
        default=None,
        metavar="ID",
        help="Override auto-generated run id for the eventlog dir.",
    )
    # WHY (C1, wave 9): --resume / --continue mirror mink's flag pair.
    persistence.add_argument(
        "--resume",
        default=None,
        metavar="ID",
        help="Resume a persisted ferret run by id.",
    )
    persistence.add_argument(
        "-c",
        "--continue",
        dest="continue_latest",
        action="store_true",
        default=False,
        help="Resume the newest ferret run under cwd.",
    )
    # WHY (FF2): sandbox-first execution.
    behavior.add_argument(
        "--sandbox",
        choices=list(_VALID_SANDBOX_MODES),
        default="read-only",
        metavar="MODE",
        help="Sandbox mode (default: read-only).",
    )
    # WHY (F1, wave 9): OS-level sandboxing for ferret.
    behavior.add_argument(
        "--os-sandbox",
        dest="os_sandbox",
        choices=list(_VALID_OS_SANDBOX_FLAGS),
        default="auto",
        metavar="OPT",
        help="OS sandbox: auto (default) | on | off.",
    )
    # WHY (P1, wave 9): execution backend for tool calls.
    behavior.add_argument(
        "--sandbox-backend",
        dest="sandbox_backend",
        choices=list(_VALID_SANDBOX_BACKENDS),
        default="local",
        metavar="BACKEND",
        help="Backend: local (default) | modal.",
    )
    # WHY (FF3): approval preset.
    behavior.add_argument(
        "--approval",
        choices=list(_VALID_APPROVAL_PRESETS),
        default="read-only",
        metavar="PRESET",
        help="Legacy 3-preset approval (default: read-only).",
    )
    # WHY (G3, w13): the standard 5-mode ``--permission-mode`` surface.
    # ``default=None`` lets us detect whether the user passed the flag
    # explicitly so we can prefer it over ``--approval`` when both are
    # set. When unset, ferret falls back to mapping ``--approval`` onto
    # an ApprovalMode (read-only -> READ_ONLY, auto -> AUTO, full ->
    # YOLO) so the legacy default keeps its current semantics.
    behavior.add_argument(
        "--permission-mode",
        dest="permission_mode",
        choices=list(_VALID_PERMISSION_MODES),
        default=None,
        metavar="M",
        help="5-mode (read-only|suggest|auto|yolo|strict).",
    )
    # WHY (G15, w13): the parity flag triplet shared by IDE-flagship CLIs.
    # ``--full-auto`` and ``--yolo`` are short-hand approval aliases;
    # ``--add-dir`` extends writable scope; ``--skip-git-repo-check``
    # bypasses the "not in a git repo" guard; ``--image`` attaches image
    # inputs; ``--profile`` overlays a TOML profile from
    # ``~/.chimera/profiles/<NAME>.toml``.
    #
    # E6-W13: ``help=argparse.SUPPRESS`` on these six flags drops them
    # from ``chimera ferret --help`` so the 50-line ceiling enforced by
    # ``tests/cli/test_help_brevity.py`` stays tight. Each one is fully
    # documented in ``_LONG_HELP`` above and surfaces normally under
    # ``chimera ferret --help-long``. Future additions should use
    # :func:`chimera.cli.help_long.register_argument` so auto-promotion
    # handles this routing automatically.
    behavior.add_argument(
        "--full-auto",
        dest="full_auto",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )
    behavior.add_argument(
        "--yolo",
        dest="yolo",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )
    behavior.add_argument(
        "--add-dir",
        dest="add_dirs",
        action="append",
        default=None,
        metavar="PATH",
        help=argparse.SUPPRESS,
    )
    behavior.add_argument(
        "--skip-git-repo-check",
        dest="skip_git_repo_check",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )
    core.add_argument(
        "--image",
        dest="images",
        action="append",
        default=None,
        metavar="PATH",
        help=argparse.SUPPRESS,
    )
    core.add_argument(
        "--profile",
        dest="profile",
        default=None,
        metavar="NAME",
        help=argparse.SUPPRESS,
    )
    # WHY (FF1): config override.
    core.add_argument(
        "--config",
        dest="config_path",
        default=None,
        metavar="PATH",
        help="Override ferret config file path.",
    )
    # WHY (FF4): ACP is the *default* serve transport.
    serve_grp.add_argument(
        "--http",
        action="store_true",
        default=False,
        help="serve: run HTTP server instead of default ACP.",
    )
    serve_grp.add_argument(
        "--host",
        default=None,
        metavar="HOST",
        help="serve --http: bind host (default: 127.0.0.1).",
    )
    serve_grp.add_argument(
        "--port",
        type=int,
        default=None,
        metavar="PORT",
        help="serve --http: bind port (default: 5174).",
    )
    serve_grp.add_argument(
        "--auth-token",
        default=None,
        metavar="TOKEN",
        help="serve --http: bearer token required on requests.",
    )
    # WHY (F1/W8): TLS pair gates the HTTP path off-localhost.
    serve_grp.add_argument(
        "--tls-cert",
        dest="tls_cert",
        default=None,
        metavar="PATH",
        help="serve --http: PEM cert (paired with --tls-key).",
    )
    serve_grp.add_argument(
        "--tls-key",
        dest="tls_key",
        default=None,
        metavar="PATH",
        help="serve --http: PEM key matching --tls-cert.",
    )
    # WHY (FF5): cloud-bridge flags. Hidden from short help (E6-W13)
    # because they only apply to the ``bridge`` subcommand; full
    # docs live in ``_LONG_HELP`` and surface under ``--help-long``.
    serve_grp.add_argument(
        "--remote-url",
        default=None,
        metavar="URL",
        help=argparse.SUPPRESS,
    )
    serve_grp.add_argument(
        "--bridge-token",
        default=None,
        metavar="TOKEN",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "subcommand",
        nargs="?",
        default=None,
        choices=list(_VALID_SUBCOMMANDS),
        metavar="SUBCOMMAND",
        help="serve | sessions | share | agents | bench | bridge.",
    )
    parser.add_argument(
        "sub_action",
        nargs="?",
        default=None,
        choices=list(_VALID_SUB_ACTIONS),
        metavar="ACTION",
        help="list | show | <suite> | status | stop.",
    )
    parser.add_argument(
        "sub_target",
        nargs="?",
        default=None,
        metavar="TARGET",
        help="Run/session id for show/share.",
    )
    # WHY (server-mgmt): ``serve stop`` knobs.
    serve_grp.add_argument(
        "--all",
        dest="serve_stop_all",
        action="store_true",
        default=False,
        help="serve stop: stop every backgrounded ferret server.",
    )
    serve_grp.add_argument(
        "--serve-timeout",
        dest="serve_stop_timeout",
        type=float,
        default=10.0,
        metavar="SEC",
        help="serve stop: SIGTERM grace window (default: 10.0).",
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
# Allowed-tools filtering — mirrors otter's helper
# ---------------------------------------------------------------------------


class _UnknownAllowedTool(ValueError):
    """Raised when ``--allowed-tools`` names a tool that doesn't exist.

    Carrying the formatted error message on the exception keeps callers
    free of presentation logic — they ``print(exc)`` and exit 2.
    """


def _filter_allowed_tools(tools: list[Any], allowed: str) -> list[Any]:
    """Return *tools* filtered to the comma-separated names in *allowed*.

    Matching is case-insensitive so frontmatter-style ``Bash,Read`` matches
    the canonical lower-case ``BashTool.name``. An unknown name raises
    :class:`_UnknownAllowedTool`.

    Args:
        tools: Source tool list (typically ``AGENT_TOOLS``).
        allowed: Raw comma-separated string from ``--allowed-tools``.
            Empty / whitespace-only entries are ignored.

    Returns:
        New filtered list. Empty *allowed* returns *tools* unchanged.

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


# ---------------------------------------------------------------------------
# Permission-mode resolution (G3, w13)
# ---------------------------------------------------------------------------


def _resolve_ferret_permissions(args: argparse.Namespace) -> Any:
    """Resolve ferret's permission policy from ``--permission-mode``/``--approval``.

    Resolution order (first match wins):

    1. ``--permission-mode`` (5-mode standard) when explicitly set.
    2. ``--approval`` (legacy 3-preset) — mapped onto an
       :class:`~chimera.permissions.modes.ApprovalMode` so a single
       :func:`~chimera.permissions.modes.policy_for_mode` codepath drives
       the live :class:`~chimera.permissions.base.PermissionPolicy`.
    3. ``ApprovalMode.READ_ONLY`` — matches ferret's documented default.

    Errors at parse or factory time degrade to ``None`` (default
    LoopConfig) with a stderr warning so a malformed flag never crashes
    the runner.

    Args:
        args: Parsed ferret argparse namespace.

    Returns:
        A live :class:`PermissionPolicy`, or ``None`` if no recognised
        flag value was found and the resolver fell through to a warning.
    """
    from chimera.permissions.modes import (
        ApprovalMode,
        parse_mode,
        policy_for_mode,
    )

    raw_mode = getattr(args, "permission_mode", None)
    raw_approval = getattr(args, "approval", None)
    yolo_flag = bool(getattr(args, "yolo", False))
    full_auto_flag = bool(getattr(args, "full_auto", False))

    # Path 1: --permission-mode wins when explicitly set. The G15 flag
    # triplet (--full-auto / --yolo) is *short-hand*, so an explicit
    # --permission-mode always overrides them.
    if raw_mode:
        try:
            return policy_for_mode(parse_mode(str(raw_mode)))
        except ValueError as exc:
            print(
                f"[ferret] --permission-mode {raw_mode!r} unrecognised "
                f"({exc}); falling back to default policy.",
                file=sys.stderr,
            )
            return None

    # Path 1b (G15, w13): --yolo / --full-auto are short-hands for the
    # corresponding --approval values. --yolo wins over --full-auto if
    # both are passed (the strictly more permissive choice "wins" so the
    # agent never silently downgrades the user's explicit intent).
    if yolo_flag:
        return policy_for_mode(ApprovalMode.YOLO)
    if full_auto_flag:
        return policy_for_mode(ApprovalMode.AUTO)

    # Path 2: legacy --approval. Route through
    # :mod:`chimera.ferret.approval` so existing FF3 tests / monkey-
    # patches (preset_from_string, policy_for_preset) keep firing.
    if raw_approval:
        try:
            from chimera.ferret import approval as _approval_mod
        except Exception:  # noqa: BLE001
            _approval_mod = None  # type: ignore[assignment]
        if _approval_mod is not None and hasattr(
            _approval_mod, "policy_for_preset"
        ):
            try:
                return _approval_mod.policy_for_preset(
                    _approval_mod.preset_from_string(str(raw_approval))
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[ferret] --approval {raw_approval!r} unrecognised "
                    f"({exc}); falling back to default policy.",
                    file=sys.stderr,
                )
                return None
        # Fallback when chimera.ferret.approval isn't importable: use
        # the central modes routing so we still produce a policy.
        try:
            return policy_for_mode(parse_mode(str(raw_approval)))
        except ValueError as exc:
            print(
                f"[ferret] --approval {raw_approval!r} unrecognised "
                f"({exc}); falling back to default policy.",
                file=sys.stderr,
            )
            return None

    # Path 3: nothing supplied — historical default is read-only.
    return policy_for_mode(ApprovalMode.READ_ONLY)


# ---------------------------------------------------------------------------
# Provider construction
# ---------------------------------------------------------------------------


def _build_provider(model: str) -> Any:
    """Construct a Provider for *model* via the ferret resolver.

    Late-binds :mod:`chimera.ferret.providers` (FF6) so the one-shot path,
    REPL, and serve paths share one routing logic. When FF6 hasn't landed
    yet, fall through to :func:`chimera.providers.factory.create_provider`
    so the scaffold remains usable end-to-end during the parallel build.

    Args:
        model: Model identifier (e.g. ``gpt-5``, ``gpt-4o``,
            ``claude-sonnet-4-6``, ``openai/gpt-5`` for OpenRouter).

    Returns:
        A live :class:`~chimera.providers.base.Provider` instance.
    """
    try:
        from chimera.ferret import providers as _ferret_providers  # type: ignore[attr-defined]
    except ImportError:
        _ferret_providers = None  # type: ignore[assignment]

    if _ferret_providers is not None and hasattr(
        _ferret_providers, "build_provider"
    ):
        return _ferret_providers.build_provider(argparse.Namespace(model=model))

    from chimera.providers.factory import create_provider

    return create_provider(model=model)


# ---------------------------------------------------------------------------
# Subcommand dispatch — placeholders filled in by sibling agents in the wave
# ---------------------------------------------------------------------------


def _dispatch_serve(args: argparse.Namespace) -> int:
    """Dispatch ``chimera ferret serve`` to ACP, HTTP, or management commands.

    Routing precedence:

    1. ``ferret serve status`` / ``ferret serve stop`` — pidfile-based
       management subcommands (server-mgmt). These don't bind a socket;
       they read ``~/.chimera/run/ferret-*.pid`` and dispatch SIGTERM (then
       SIGKILL on timeout) per the graceful-shutdown rule in CLAUDE.md.
    2. ``--http`` — boot the HTTP + SSE server.
    3. Default — run the IDE-first ACP server via
       :func:`chimera.ferret.ide.maybe_serve_ide_acp`.

    FF4 owns the IDE-first ACP schema; the HTTP variant is opt-in. When
    ``--http`` is set, F1/W8 wires the HTTP + SSE server by delegating
    to :func:`chimera.otter.server.serve_http` with a ferret-flavored
    agent factory (provider via FF6, sandbox via FF2, approval via FF3).
    """
    sub_action = getattr(args, "sub_action", None)
    if sub_action in ("status", "stop"):
        # Reuse the otter management dispatcher — pidfile layout is shared
        # across flavors (only the prefix differs).
        from chimera.otter.cli import _dispatch_serve_management

        return _dispatch_serve_management(
            args, action=sub_action, prefix="ferret",
        )
    if not getattr(args, "http", False):
        # WHY: ACP is the IDE-first default. Late-bind so cli.py loads even
        # if FF4 hasn't shipped, and so ``--help`` stays cheap.
        try:
            from chimera.ferret.ide import maybe_serve_ide_acp
        except Exception as exc:  # noqa: BLE001
            print(
                f"ferret serve: ACP transport unavailable ({exc}). "
                "Pass --http for the HTTP server.",
                file=sys.stderr,
            )
            return 2
        rc = maybe_serve_ide_acp(args)
        if rc is not None:
            return int(rc)
        # ACP module declined (rc=None): fall through to HTTP. Rare; the
        # current ACP helper only returns ``None`` when ``--http`` is set,
        # which is already handled above.
        return _dispatch_serve_http(args)
    return _dispatch_serve_http(args)


# Default HTTP bind port for ``chimera ferret serve --http``. Distinct from
# the otter default (5173) so the two servers can coexist on a single host.
_FERRET_DEFAULT_HTTP_PORT = 5174


def _dispatch_serve_http(args: argparse.Namespace) -> int:
    """Run the HTTP + SSE ferret server.

    Thin wrapper around :func:`chimera.otter.server.serve_http`. The
    server protocol (``/healthz``, ``/session``, SSE event stream,
    ``/tool/approve``) is identical to otter's; what differs is the
    per-session agent factory: ferret routes through its own provider
    chain (FF6), sandbox wrapper (FF2), and approval preset (FF3).

    All heavy imports (``Agent``, ``ReAct``, ``LocalEnvironment``,
    ``OtterSessionState``) stay inside the function so ``chimera ferret
    --help`` and ``chimera ferret serve --help`` remain cheap.

    Args:
        args: Parsed argparse namespace. Reads ``host``, ``port``,
            ``auth_token``, ``tls_cert``, ``tls_key``, ``model``,
            ``cwd``, ``max_steps``, ``sandbox``, ``approval``.

    Returns:
        Process exit code: 0 on graceful shutdown, 2 on usage error
        (e.g. half-paired ``--tls-cert`` / ``--tls-key``).
    """
    from chimera.core.agent import Agent
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.core.prompt import Prompt
    from chimera.core.tool_group import AGENT_TOOLS
    from chimera.env.local import LocalEnvironment
    from chimera.events.base import EventBus
    from chimera.ferret.ide import IDENotificationEmitter, ide_emit_for_state
    from chimera.otter.server import (
        DEFAULT_HOST,
        OtterSessionState,
        serve_http,
    )

    cwd = os.path.abspath(getattr(args, "cwd", None) or os.getcwd())
    model = getattr(args, "model", None) or _DEFAULT_MODEL
    max_steps = int(getattr(args, "max_steps", 50) or 50)

    host = str(getattr(args, "host", None) or DEFAULT_HOST)
    port = int(getattr(args, "port", None) or _FERRET_DEFAULT_HTTP_PORT)
    auth_token = getattr(args, "auth_token", None)
    tls_cert = getattr(args, "tls_cert", None)
    tls_key = getattr(args, "tls_key", None)
    # WHY: surface the typo-paired-flag mistake here so the user gets a
    # CLI-level error before any provider/sandbox wiring fires. Mirrors
    # otter's ``_dispatch_serve_http`` contract.
    if bool(tls_cert) ^ bool(tls_key):
        print(
            "error: --tls-cert and --tls-key must be set together",
            file=sys.stderr,
        )
        return 2

    sandbox_value = getattr(args, "sandbox", "read-only") or "read-only"
    # WHY (G3, w13): approval/permission-mode resolution is delegated to
    # ``_resolve_ferret_permissions`` so the HTTP factory below uses the
    # exact same routing as the ``-p`` one-shot path.
    # WHY (F2/W9): the IDE-friendly notification kinds (``code/diff``,
    # ``editor/open_file``, ``terminal/output``, ``progress/step``) are
    # ferret-specific. The same ``--ide-schema`` flag the ACP transport
    # honors flips them on/off here too — when ``False`` we still build
    # an :class:`EventBus` for any other listener but skip wiring the
    # IDE translator, so HTTP-only relays that don't speak the rich
    # schema see only the otter base ``loop_event`` / ``result`` shapes.
    ide_schema = bool(getattr(args, "ide_schema", True))

    def _factory(state: OtterSessionState) -> Any:
        # Provider — late-bind FF6 so the factory uses the ferret chain
        # (gpt-5 → gpt-4o → claude-sonnet-4-6 → openrouter), with a
        # generic fallback when FF6 is absent.
        provider = _build_provider(model)

        # Environment — wrap LocalEnvironment with the ferret sandbox
        # (FF2) per ``--sandbox``. Falls through to the unsandboxed env
        # when the sandbox module isn't importable, matching the print-
        # mode contract.
        workdir = state.working_dir or cwd
        base_env = LocalEnvironment(workdir=workdir)
        base_env.setup()
        env: Any = base_env
        try:
            from chimera.ferret import sandbox as _sandbox_mod

            mode = _sandbox_mod.parse_sandbox_mode(sandbox_value)
            env = _sandbox_mod.SandboxedEnvironment(
                base_env,
                mode=mode,
                os_sandbox=getattr(args, "os_sandbox", "auto") or "auto",
            )
        except Exception:  # noqa: BLE001 - keep base env on missing/error
            env = base_env

        # Approval preset (FF3) → LoopConfig.permissions.
        # WHY (G3, w13): unified ``--permission-mode`` / ``--approval``
        # routing — same shared helper the ``-p`` path uses, so the HTTP
        # server and the one-shot runner agree on what each flag means.
        permissions = _resolve_ferret_permissions(args)

        # WHY (F2/W9): per-session :class:`EventBus` carries
        # :class:`ToolCallEvent` / :class:`ToolResultEvent` published by
        # the loop. The :class:`IDENotificationEmitter` subscribes to
        # those and fans them out as IDE-shaped SSE frames on ``state``'s
        # event stream — same JSON shape the ACP transport already
        # ships, just delivered over HTTP+SSE. Wiring the bus on
        # :class:`LoopConfig.event_bus` is the documented hook; an
        # explicit instance keeps each session's translation state
        # (pending tool calls, terminal sequence numbers) isolated.
        event_bus = EventBus()
        emitter = IDENotificationEmitter(
            ide_emit_for_state(state),
            ide_schema=ide_schema,
        )
        emitter.attach(event_bus)
        config = LoopConfig(permissions=permissions, event_bus=event_bus)
        loop = ReAct(max_steps=max_steps, config=config)
        prompt = Prompt.from_string(
            "You are Ferret, a Chimera coding agent driven over HTTP."
        )
        agent = Agent(
            provider=provider,
            tools=list(AGENT_TOOLS),
            loop=loop,
            prompt=prompt,
        )
        # Surface the sandboxed env onto the agent so future tool calls
        # routed through ``state.agent`` honor the per-session sandbox.
        # OtterServer's ``_drive_agent`` passes ``env=None`` (the agent
        # carries its own env reference), so we attach via attribute for
        # downstream tooling that expects ``agent.env``.
        try:
            agent.env = env  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - best-effort, never crash factory
            pass
        return agent

    scheme = "https" if (tls_cert and tls_key) else "http"
    sys.stderr.write(
        f"[ferret] HTTP server listening on {scheme}://{host}:{port}\n"
    )
    sys.stderr.flush()
    return serve_http(
        _factory,
        host=host,
        port=port,
        auth_token=auth_token,
        tls_cert=tls_cert,
        tls_key=tls_key,
        # WHY (server-mgmt): write ``~/.chimera/run/ferret-<port>.pid`` so a
        # separate shell can run ``chimera ferret serve status`` / ``stop``
        # against this backgrounded process.
        pidfile_prefix="ferret",
    )


def _dispatch_sessions(args: argparse.Namespace) -> int:
    """Wire ``chimera ferret sessions [list|show <id>]`` to FF1's handler.

    The wave-1 scaffold parser puts the sessions sub-action under
    ``args.sub_action`` and the optional id under ``args.sub_target``.
    :func:`chimera.ferret.sessions.dispatch_sessions` expects
    ``args.sessions_command="sessions"`` plus per-action filter dests.
    Read raw attributes off the namespace, fall back to sensible
    defaults, and forward.
    """
    try:
        from chimera.ferret.sessions import dispatch_sessions
    except Exception as exc:  # noqa: BLE001
        print(
            f"ferret sessions: handler unavailable ({exc})", file=sys.stderr,
        )
        return 2

    args.sessions_command = "sessions"
    args.sessions_action = getattr(args, "sub_action", None) or "list"
    args.sessions_target = getattr(args, "sub_target", None)
    args.sessions_since = getattr(args, "sessions_since", None)
    args.sessions_model = getattr(args, "sessions_model", None)
    args.sessions_limit = getattr(args, "sessions_limit", 50)
    args.sessions_json = getattr(args, "sessions_json", False)
    # B9-W11
    args.sessions_all_clis = getattr(args, "sessions_all_clis", False)
    if not hasattr(args, "full"):
        args.full = True
    rc = dispatch_sessions(args)
    return rc if rc is not None else 0


def _dispatch_share(args: argparse.Namespace) -> int:
    """Stub for ``chimera ferret share <session>``.

    A future agent (FF5 cloud bridge) owns the share body. Returning 2
    keeps shell pipelines from silently treating an unimplemented
    command as success.
    """
    target = getattr(args, "sub_action", None)
    print(
        f"ferret share: target={target!r} (scaffold; see "
        "research/ferret/SPEC.md, agent FF5).",
        file=sys.stderr,
    )
    return 2


def _dispatch_agents(args: argparse.Namespace) -> int:
    """Wire ``chimera ferret agents [list|show <name>]`` to the FF7 handlers.

    Routes through :func:`chimera.ferret.agents.cmd_agents_list` and
    :func:`chimera.ferret.agents.cmd_agents_show`. The handler module is
    late-bound so a missing ``chimera.ferret.agents`` falls back to a
    scaffold message with rc=2.
    """
    action = getattr(args, "sub_action", None) or "list"
    target = getattr(args, "sub_target", None)
    no_color = bool(getattr(args, "no_color", False) or getattr(args, "no_rich", False))
    try:
        from chimera.ferret.agents import cmd_agents_list, cmd_agents_show
    except Exception as exc:  # noqa: BLE001
        print(
            f"ferret agents: handler unavailable ({exc}). action={action!r} "
            f"target={target!r}.",
            file=sys.stderr,
        )
        return 2
    # Status line — keeps the "ferret agents" tag in stderr so callers /
    # CI greps that key off the prefix continue to work.
    print(
        f"ferret agents: action={action!r} target={target!r}",
        file=sys.stderr,
    )
    if action == "list":
        return int(cmd_agents_list(no_color=no_color))
    if action == "show":
        return int(cmd_agents_show(target, no_color=no_color))
    print(
        f"ferret agents: unknown action {action!r} (use 'list' or 'show').",
        file=sys.stderr,
    )
    return 2


def _dispatch_bench(args: argparse.Namespace) -> int:
    """Stub for ``chimera ferret bench <suite>``.

    A future agent owns the benchmark suite wiring (mirrors otter's
    ``bench`` surface). Returning 2 keeps the scaffold contract honest.
    """
    suite = getattr(args, "sub_action", None)
    print(
        f"ferret bench: suite={suite!r} (scaffold; see "
        "research/ferret/SPEC.md).",
        file=sys.stderr,
    )
    return 2


def _default_bridge_inbound_handler(message: Any) -> None:
    """Default no-op ``inbound_handler`` used by ``ferret bridge``.

    The bridge spec leaves wiring of inbound prompts to the local agent
    as a wave-9 concern (live REPL attachment). Until that lands, the
    CLI dispatcher uses a stderr-logging handler so operators can verify
    the round-trip without a live agent. The handler stays synchronous
    on purpose — the bridge owns its own daemon thread.
    """
    text = getattr(message, "text", "")
    msg_id = getattr(message, "message_id", "")
    print(
        f"[ferret bridge] inbound message_id={msg_id!r} text={text!r}",
        file=sys.stderr,
    )


def _dispatch_bridge(args: argparse.Namespace) -> int:
    """Dispatch ``chimera ferret bridge`` to the FF5 cloud-bridge runner.

    Reads ``--remote-url`` and ``--bridge-token`` (with fallbacks
    documented in :mod:`chimera.ferret.cloud_bridge`), connects, and
    blocks on the inbound poll loop until ``Ctrl-C``. Late-binds the
    cloud-bridge module so an absent FF5 surfaces a friendly error
    rather than an :class:`ImportError` traceback.

    Returns:
        Process exit code: 0 on graceful shutdown, 2 on auth failure or
        when FF5 is missing, 1 on any other bridge-level error.
    """
    try:
        from chimera.ferret import cloud_bridge as _cloud_bridge
    except Exception as exc:  # noqa: BLE001
        print(
            f"ferret bridge: cloud-bridge module unavailable ({exc}). "
            "See research/ferret/SPEC.md (FF5).",
            file=sys.stderr,
        )
        return 2
    return int(_cloud_bridge.run_bridge(args, _default_bridge_inbound_handler))


_SUBCOMMAND_DISPATCH: dict[str, Any] = {
    "serve": _dispatch_serve,
    "sessions": _dispatch_sessions,
    "share": _dispatch_share,
    "agents": _dispatch_agents,
    "bench": _dispatch_bench,
    "bridge": _dispatch_bridge,
}


# ---------------------------------------------------------------------------
# One-shot --print path with sandbox + approval + provider wiring
# ---------------------------------------------------------------------------


def _run_print_mode(args: argparse.Namespace) -> int:
    """Run ``chimera ferret -p PROMPT`` with full sandbox + approval wiring.

    This is the wave-6 "live-driven" one-shot path: it wraps the
    :class:`~chimera.env.local.LocalEnvironment` with a
    :class:`~chimera.ferret.sandbox.SandboxedEnvironment` per ``--sandbox``
    and constructs a :class:`~chimera.core.loop_config.LoopConfig` whose
    :attr:`permissions` slot is populated from
    :func:`chimera.ferret.approval.policy_for_preset` per ``--approval``.
    The provider is resolved through
    :func:`chimera.ferret.providers.build_provider` (FF6) so the OpenAI-
    flagship chain (gpt-5 → gpt-4o → claude-sonnet-4-6 → openai/gpt-5
    via OpenRouter, plus ``:cloud`` Ollama tags) is honored.

    Late-binds every sibling import so an absent module degrades to a
    sensible default rather than crashing the runner.

    Args:
        args: Parsed ferret namespace; reads ``print_mode``, ``model``,
            ``cwd``, ``max_steps``, ``output_format``, ``sandbox``,
            ``approval``.

    Returns:
        Process exit code: 0 on success, 1 on agent failure, 2 on usage
        error, 130 on cancellation.
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

    prompt_text = getattr(args, "print_mode", None)
    if not prompt_text:
        print("ferret -p: missing PROMPT argument", file=sys.stderr)
        return 2

    cwd = os.path.abspath(getattr(args, "cwd", None) or os.getcwd())
    output_format = getattr(args, "output_format", "text") or "text"

    # 1. Provider (FF6) — late-bind, fall back to generic factory.
    _providers_mod: Any = None
    try:
        import chimera.ferret.providers as _providers_mod  # noqa: F811
    except Exception:  # noqa: BLE001
        _providers_mod = None
    if _providers_mod is not None and hasattr(_providers_mod, "build_provider"):
        try:
            provider = _providers_mod.build_provider(args)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    else:
        from chimera.providers.factory import create_provider

        provider = create_provider(model=getattr(args, "model", None))

    # 2. Environment + sandbox (FF2) — wrap LocalEnvironment when available.
    # WHY (P1, wave 9): ``--sandbox-backend modal`` swaps LocalEnvironment
    # for :class:`chimera.env.modal_sandbox.ModalSandboxEnvironment`. When
    # the optional ``modal`` extra isn't installed we warn once and fall
    # back to local so the run still proceeds.
    sandbox_backend = getattr(args, "sandbox_backend", "local") or "local"
    base_env: Any
    if sandbox_backend == "modal":
        try:
            from chimera.env.modal_sandbox import ModalSandboxEnvironment

            base_env = ModalSandboxEnvironment(workdir=cwd)
            base_env.setup()
        except ImportError as exc:
            print(
                f"[ferret] --sandbox-backend modal requested but modal is "
                f"unavailable ({exc}); falling back to local.",
                file=sys.stderr,
            )
            base_env = LocalEnvironment(workdir=cwd)
            base_env.setup()
    else:
        base_env = LocalEnvironment(workdir=cwd)
        base_env.setup()
    env: Any = base_env
    _sandbox_mod: Any = None
    try:
        import chimera.ferret.sandbox as _sandbox_mod  # noqa: F811 — module ref.
    except Exception:  # noqa: BLE001 — FF2 not present; keep LocalEnvironment.
        _sandbox_mod = None
    if _sandbox_mod is not None and hasattr(_sandbox_mod, "SandboxedEnvironment"):
        try:
            mode = _sandbox_mod.parse_sandbox_mode(
                getattr(args, "sandbox", "read-only")
            )
            env = _sandbox_mod.SandboxedEnvironment(
                base_env,
                mode=mode,
                os_sandbox=getattr(args, "os_sandbox", "auto") or "auto",
            )
        except Exception as exc:  # noqa: BLE001 — keep base env on parse error.
            print(
                f"[ferret] --sandbox {getattr(args, 'sandbox', None)!r} "
                f"unrecognised ({exc}); falling back to unsandboxed env.",
                file=sys.stderr,
            )

    # 3. Approval (FF3) — populate LoopConfig.permissions from preset.
    # WHY (G3, w13): ``--permission-mode`` (5-mode standard) wins over the
    # legacy ``--approval`` (3-preset) when explicitly set. Otherwise we
    # map the legacy approval value onto an ApprovalMode so the in-tree
    # routing stays single-codepath.
    permissions = _resolve_ferret_permissions(args)

    cancel = CancellationToken()
    config = LoopConfig(cancellation=cancel, permissions=permissions)
    loop = ReAct(
        max_steps=int(getattr(args, "max_steps", 50) or 50),
        config=config,
    )
    base_prompt = (
        "You are Ferret, a Chimera coding agent. Plan briefly, then act."
    )
    # WHY (W13-G2): pull AGENTS.md / CLAUDE.md walk-up instructions into the
    # system prompt so a ferret session sees the same project guidance the
    # Codex/Claude-Code reference CLIs honour. Late-binds the loader so the
    # no-files case is free.
    try:
        from chimera.cli.instruction_files import load_instruction_text

        _instruction_text = load_instruction_text(project_dir=cwd)
    except Exception:  # noqa: BLE001 — best-effort, never block ferret startup.
        _instruction_text = ""
    if _instruction_text:
        base_prompt = base_prompt + "\n\n" + _instruction_text
    chimera_prompt = Prompt.from_string(base_prompt)
    tools = list(AGENT_TOOLS)
    allowed = getattr(args, "allowed_tools", "") or ""
    if allowed:
        try:
            tools = _filter_allowed_tools(tools, allowed)
        except _UnknownAllowedTool as exc:
            print(str(exc), file=sys.stderr)
            base_env.cleanup()
            return 2
    agent = Agent(
        provider=provider,
        tools=tools,
        loop=loop,
        prompt=chimera_prompt,
    )

    # WHY (C1, wave 9): apply ``--resume`` / ``-c`` before dispatching to
    # the agent. Either flag prepends a ``<prior_conversation>`` block
    # rendered from the resumed eventlog so the agent's first turn has
    # the full prior context. No-op when neither flag is set.
    effective_prompt = _apply_ferret_resume_prefix(args, default_prompt=prompt_text)
    # WHY (G15, w13): ``--image PATH`` attaches each path as an annotation
    # to the user message. The agent can fetch the bytes via the
    # ``read_image`` tool — we do not pre-encode here so the prompt
    # stays small and the model decides what to inspect.
    effective_prompt = _apply_ferret_image_prefix(args, prompt=effective_prompt)

    try:
        result = asyncio.run(agent.async_run(effective_prompt, env=env))
    except KeyboardInterrupt:
        cancel.cancel()
        print("\n[cancelled]", file=sys.stderr)
        return 130
    finally:
        base_env.cleanup()

    if output_format == "json":
        payload = {
            "output": getattr(result, "output", ""),
            "steps": getattr(result, "steps", 0),
            "cost": getattr(result, "cost", 0.0),
            "success": getattr(result, "success", False),
            "model": getattr(provider, "model_name", getattr(args, "model", "")),
        }
        json.dump(payload, sys.stdout)
        sys.stdout.write("\n")
    else:
        out = getattr(result, "output", None)
        if out:
            print(out)
    return 0 if getattr(result, "success", False) else 1


def _apply_ferret_resume_prefix(
    args: argparse.Namespace,
    *,
    default_prompt: str,
) -> str:
    """Resolve ``--resume`` / ``--continue`` for ferret.

    Symmetric helper to otter's ``_apply_resume_prefix`` — see that
    docstring for the broader rationale. Prefix is hard-coded to
    ``ferret-`` because each CLI carries its own.

    Args:
        args: The parsed ferret argparse namespace.
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
        prefix="ferret-",
        eventlog_root=default_eventlog_root(),
        cwd=os.path.abspath(getattr(args, "cwd", None) or os.getcwd()),
    )
    if target_id is None:
        return default_prompt

    try:
        session = resume_run(target_id)
    except (ValueError, OSError) as exc:
        print(
            f"[ferret] --resume / --continue: failed to load run "
            f"{target_id!r}: {exc}",
            file=sys.stderr,
        )
        return default_prompt

    messages = list(getattr(session, "messages", []) or [])
    if not messages:
        return default_prompt

    sys.stderr.write(
        f"[ferret] resumed run {target_id} ({len(messages)} messages)\n"
    )
    sys.stderr.flush()
    transcript = build_resume_prefix(messages)
    return f"{transcript}{default_prompt}"


# ---------------------------------------------------------------------------
# G15 helpers — profile overlay, yolo warning, git-repo guard, image prefix
# ---------------------------------------------------------------------------


def _apply_ferret_image_prefix(
    args: argparse.Namespace,
    *,
    prompt: str,
) -> str:
    """Prepend an ``<attached_images>`` block to *prompt* for ``--image``.

    Each ``--image PATH`` becomes one bullet in the block; the agent can
    read the bytes via the ``read_image`` tool. Missing files emit a
    stderr notice but do not abort the run — the agent decides whether
    the missing image is a fatal problem for the task.
    """
    images = getattr(args, "images", None) or []
    if not images:
        return prompt
    lines: list[str] = []
    for raw_path in images:
        if not isinstance(raw_path, str) or not raw_path:
            continue
        full = os.path.expanduser(raw_path)
        if not os.path.isfile(full):
            sys.stderr.write(
                f"[ferret] --image: file not found: {raw_path!r}\n"
            )
            continue
        lines.append(f"- {full}")
    if not lines:
        return prompt
    block = (
        "<attached_images>\n"
        + "\n".join(lines)
        + "\nUse the read_image tool to inspect any of the paths above."
        + "\n</attached_images>\n\n"
    )
    return f"{block}{prompt}"


# ---------------------------------------------------------------------------
# G15 helpers — profile overlay, yolo warning, git-repo guard
# ---------------------------------------------------------------------------


_FERRET_PROFILES_DIR = "~/.chimera/profiles"
"""Documented location of TOML profile overlays (see ``--profile``)."""

# Argparse dest names that ``--profile`` is allowed to overlay. Limiting
# the surface keeps the TOML from being able to spoof random attributes
# onto the namespace (e.g. ``subcommand``).
_PROFILE_OVERLAY_KEYS: tuple[str, ...] = (
    "model",
    "sandbox",
    "approval",
    "permission_mode",
    "max_steps",
    "allowed_tools",
    "add_dirs",
    "images",
    "skip_git_repo_check",
    "full_auto",
    "yolo",
    "output_format",
    "cwd",
)


def _apply_ferret_profile(args: argparse.Namespace) -> None:
    """Overlay ``~/.chimera/profiles/<NAME>.toml`` onto ``args`` in-place.

    Reads the named profile (no-op when ``--profile`` is unset), parses
    it as TOML, and copies any whitelisted key onto the parsed args
    namespace. Profile keys do *not* override values the user passed
    explicitly on the command line — only those still at their argparse
    default.

    Errors (missing file, parse failure, unknown key) print a stderr
    notice and continue with the un-overlaid args so a typo never
    crashes ferret startup.
    """
    name = getattr(args, "profile", None)
    if not name:
        return
    path = os.path.expanduser(os.path.join(_FERRET_PROFILES_DIR, f"{name}.toml"))
    if not os.path.isfile(path):
        print(
            f"[ferret] --profile {name!r}: file not found at {path}; "
            "continuing without overlay.",
            file=sys.stderr,
        )
        return
    try:
        import tomllib
    except ImportError:  # pragma: no cover — chimera requires Python 3.11+.
        return
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        print(
            f"[ferret] --profile {name!r}: failed to load ({exc}); "
            "continuing without overlay.",
            file=sys.stderr,
        )
        return
    if not isinstance(data, dict):
        return
    applied: list[str] = []
    for key, value in data.items():
        if key not in _PROFILE_OVERLAY_KEYS:
            continue
        # The argparse defaults for our G15 list-typed flags are ``None``
        # (so we can detect "user did not pass") and ``False`` for the
        # boolean shortcuts. Only overlay when the slot is still at the
        # argparse default — never clobber an explicit CLI value.
        current = getattr(args, key, None)
        if current is None or current is False or current == "":
            setattr(args, key, value)
            applied.append(key)
    if applied:
        sys.stderr.write(
            f"[ferret] --profile {name!r}: overlaid "
            f"{', '.join(sorted(applied))}\n"
        )
        sys.stderr.flush()


def _emit_yolo_warning(args: argparse.Namespace) -> None:
    """Print a stderr warning when ``--yolo`` is active.

    The warning is intentionally noisy (every invocation, not once per
    session) because the flag silently bypasses every approval gate; an
    operator who wants quiet has to either drop the flag or accept the
    line of stderr.
    """
    if not bool(getattr(args, "yolo", False)):
        return
    sys.stderr.write(
        "[ferret] WARNING: --yolo bypasses every approval gate. "
        "Tool calls run without prompting. Use only on trusted code.\n"
    )
    sys.stderr.flush()


def _is_inside_git_repo(start: str | None = None) -> bool:
    """Return ``True`` when ``start`` (or cwd) is inside a git work tree.

    Uses :func:`os.path.isdir` to walk up looking for a ``.git`` entry
    so we don't depend on the ``git`` binary being installed. A bare
    repo (``.git`` is a file, not a directory) still counts.
    """
    cur = os.path.abspath(start or os.getcwd())
    while True:
        marker = os.path.join(cur, ".git")
        if os.path.exists(marker):
            return True
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def _check_git_repo_guard(args: argparse.Namespace) -> int | None:
    """Print a warning when ferret is launched outside a git repo.

    The guard is *advisory* by default — it warns once on stderr so
    operators know edits won't be tracked, but does not block the run.
    ``--skip-git-repo-check`` silences the warning entirely. Returning
    ``None`` always means "continue"; the function only returns an
    integer when a future iteration tightens the guard into a hard
    block (kept as the documented escape hatch).
    """
    if bool(getattr(args, "skip_git_repo_check", False)):
        return None
    cwd = os.path.abspath(getattr(args, "cwd", None) or os.getcwd())
    if _is_inside_git_repo(cwd):
        return None
    sys.stderr.write(
        f"[ferret] warning: cwd {cwd!r} is not inside a git repository; "
        "agent edits won't be tracked. Pass --skip-git-repo-check to "
        "silence this warning.\n"
    )
    sys.stderr.flush()
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@friendly_errors
def run(args: argparse.Namespace) -> int:
    """Entry point invoked by ``chimera ferret``.

    Args:
        args: Parsed ``argparse.Namespace`` from the ferret subparser.

    Returns:
        Process exit code (``0`` on success).
    """
    # A10-W11: ``--help-long`` shows standard help + long flag descriptions.
    if getattr(args, "help_long", False):
        from chimera.cli.help_long import print_help_long

        print_help_long(_PARSER, _LONG_HELP)
        return 0

    # G15 (w13): apply --profile overlay first so subsequent resolution
    # (provider, sandbox, approval) sees the merged state.
    _apply_ferret_profile(args)
    # Stderr warning happens regardless of subcommand so ``ferret --yolo
    # serve`` users see the same alert as ``ferret --yolo -p ...`` users.
    _emit_yolo_warning(args)
    # Git-repo guard runs before any subcommand body fires; --skip-git-repo-check
    # is the documented opt-out. Subcommands that operate on artifacts
    # outside a checkout (e.g. ``serve stop``) bypass via the flag.
    rc = _check_git_repo_guard(args)
    if rc is not None:
        return rc

    sub = getattr(args, "subcommand", None)
    if sub in _SUBCOMMAND_DISPATCH:
        handler = _SUBCOMMAND_DISPATCH[sub]
        return int(handler(args))

    if getattr(args, "print_mode", None) is not None:
        # Wave-6: full one-shot path with sandbox + approval + provider
        # wiring. Falls back to the wave-5 REPL one-shot only if the
        # internal entry point fails to import (defence in depth).
        try:
            return int(_run_print_mode(args))
        except Exception as exc:  # noqa: BLE001
            print(
                f"ferret -p: one-shot path failed ({exc}). "
                "See research/ferret/SPEC.md (FF2/FF3/FF6).",
                file=sys.stderr,
            )
            return 2

    # No print, no subcommand — emit a brief usage hint pointing at the
    # interactive REPL. Returning 2 (usage) is conventional for "user
    # forgot --print".
    try:
        from chimera.ferret.repl import run_ferret_repl

        return int(run_ferret_repl(args))
    except Exception as exc:  # noqa: BLE001
        print(
            "ferret: interactive REPL not yet wired in this scaffold "
            f"({exc}). Use --print/-p PROMPT for one-shot mode, "
            "--version for version, or --help for the full flag list. "
            "(see research/ferret/SPEC.md).",
            file=sys.stderr,
        )
        return 2


__all__ = [
    "add_arguments",
    "run",
]
