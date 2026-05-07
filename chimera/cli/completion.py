"""Shell completion script generation for the ``chimera`` CLI.

Generates dynamic completion scripts for bash, zsh, and fish by walking the
top-level argparse parser. Users source the output into their shell config::

    chimera completion bash >> ~/.bashrc
    chimera completion zsh  >> ~/.zshrc
    chimera completion fish >  ~/.config/fish/completions/chimera.fish

Or have chimera do the wiring for them::

    chimera completion install                 # auto-detects $SHELL
    chimera completion install --shell bash    # explicit
    chimera completion install --undo          # remove the wiring

Stdlib-only. Discovery is structural — when a new subcommand is added to
``build_parser`` it shows up in the generated completions automatically.

The optional ``--cli`` filter narrows the surface to a single coding-agent
sub-CLI (mink/otter/ferret/weasel/shrew/stoat/badger) so users who only ever
invoke one agent can keep their completion table small.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from typing import TextIO

#: Sub-CLIs that count as "coding agents". Used by the ``--cli`` filter.
CODING_AGENT_CLIS: tuple[str, ...] = (
    "mink",
    "otter",
    "ferret",
    "weasel",
    "shrew",
    "stoat",
    "badger",
)


def _iter_subparsers(
    parser: argparse.ArgumentParser,
) -> Iterable[tuple[str, argparse.ArgumentParser]]:
    """Yield ``(name, subparser)`` pairs from a parser's subparsers action.

    Only yields entries whose key matches the actual subparser name (argparse
    stores aliases in ``choices`` keyed by both the canonical name and any
    aliases — we filter to one entry per parser instance to avoid duplicates).
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            seen: set[int] = set()
            for name, subparser in action.choices.items():
                key = id(subparser)
                if key in seen:
                    continue
                seen.add(key)
                yield name, subparser


def _collect_flags(parser: argparse.ArgumentParser) -> list[str]:
    """Return the long/short flag option strings declared on a parser.

    Excludes positional arguments and the auto-injected ``--help``/``-h``
    pair (shells already complete those via the help hook). Sorted for
    deterministic output across runs.
    """
    flags: set[str] = set()
    for action in parser._actions:
        if not action.option_strings:
            continue
        for opt in action.option_strings:
            if opt in ("-h", "--help"):
                continue
            flags.add(opt)
    return sorted(flags)


def _filter_subcommands(
    pairs: list[tuple[str, argparse.ArgumentParser]],
    cli_filter: str,
) -> list[tuple[str, argparse.ArgumentParser]]:
    """Apply the ``--cli`` filter to the discovered subparser list."""
    if cli_filter == "all":
        return pairs
    if cli_filter not in CODING_AGENT_CLIS:
        # Unknown filter keys fall through to "all" rather than erroring at
        # generation time — the argparse layer above is responsible for
        # validating the choice.
        return pairs
    # Always include the completion subcommand itself so users can still
    # tab-complete `chimera completion ...` after sourcing a filtered script.
    return [
        (name, sub)
        for name, sub in pairs
        if name == cli_filter or name == "completion"
    ]


def _bash_script(
    top_flags: list[str],
    subs: list[tuple[str, argparse.ArgumentParser]],
) -> str:
    """Render a bash completion script using ``complete -F``.

    The function inspects ``COMP_CWORD`` to decide whether the user is
    completing a subcommand (cword == 1) or a flag inside a subcommand
    (cword >= 2), then emits the matching candidate set via ``compgen``.
    """
    sub_names = " ".join(name for name, _ in subs)
    case_lines: list[str] = []
    for name, sub in subs:
        flags = _collect_flags(sub)
        flags_joined = " ".join(flags) if flags else ""
        case_lines.append(f'        {name})')
        case_lines.append(f'            opts="{flags_joined}"')
        case_lines.append('            ;;')
    case_block = "\n".join(case_lines) if case_lines else "        *) opts=\"\" ;;"

    top_joined = " ".join(top_flags)

    return f"""# chimera bash completion (generated)
_chimera_completions() {{
    local cur prev words cword
    COMPREPLY=()
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    if [ "${{COMP_CWORD}}" -eq 1 ]; then
        local subs="{sub_names}"
        local topflags="{top_joined}"
        COMPREPLY=( $(compgen -W "${{subs}} ${{topflags}}" -- "${{cur}}") )
        return 0
    fi
    local sub="${{COMP_WORDS[1]}}"
    local opts=""
    case "${{sub}}" in
{case_block}
    esac
    COMPREPLY=( $(compgen -W "${{opts}}" -- "${{cur}}") )
    return 0
}}
complete -F _chimera_completions chimera
"""


def _zsh_script(
    top_flags: list[str],
    subs: list[tuple[str, argparse.ArgumentParser]],
) -> str:
    """Render a zsh ``compdef`` script that uses ``_arguments`` per subcommand."""
    sub_descriptions: list[str] = []
    for name, sub in subs:
        # zsh uses the description in the completion menu — fall back to the
        # subcommand name when no help text is set.
        desc = (sub.description or name).replace("'", "''").replace(":", " -")
        sub_descriptions.append(f"    '{name}:{desc}'")
    sub_desc_block = "\n".join(sub_descriptions)

    case_lines: list[str] = []
    for name, sub in subs:
        flags = _collect_flags(sub)
        if flags:
            quoted = " ".join(f"'{f}'" for f in flags)
            case_lines.append(f"        {name})")
            case_lines.append(f"            _arguments {quoted}")
            case_lines.append("            ;;")
        else:
            case_lines.append(f"        {name})")
            case_lines.append("            ;;")
    case_block = "\n".join(case_lines)

    top_quoted = " ".join(f"'{f}'" for f in top_flags) if top_flags else ""

    return f"""#compdef chimera
# chimera zsh completion (generated)
_chimera() {{
    local context state line
    typeset -A opt_args

    _arguments -C \\
        {top_quoted} \\
        '1: :->subcmd' \\
        '*::arg:->args'

    case $state in
        subcmd)
            local -a subcommands
            subcommands=(
{sub_desc_block}
            )
            _describe 'subcommand' subcommands
            ;;
        args)
            case $words[1] in
{case_block}
            esac
            ;;
    esac
}}
compdef _chimera chimera
"""


def _fish_script(
    top_flags: list[str],
    subs: list[tuple[str, argparse.ArgumentParser]],
) -> str:
    """Render a fish completion script via ``complete -c chimera``."""
    lines: list[str] = ["# chimera fish completion (generated)"]
    # Subcommand completions — only suggested at position 1 (no prior subcmd
    # has been seen yet). The ``__fish_use_subcommand`` helper is the standard
    # idiom for this.
    for name, sub in subs:
        desc = (sub.description or name).replace("'", "\\'")
        lines.append(
            f"complete -c chimera -f -n '__fish_use_subcommand' -a '{name}' -d '{desc}'"
        )
    # Top-level flags — also gated on "no subcommand yet".
    for flag in top_flags:
        opt = flag.lstrip("-")
        if flag.startswith("--"):
            lines.append(
                f"complete -c chimera -f -n '__fish_use_subcommand' -l '{opt}'"
            )
        else:
            lines.append(
                f"complete -c chimera -f -n '__fish_use_subcommand' -s '{opt}'"
            )
    # Per-subcommand flag completions, gated on "the seen subcommand is X".
    for name, sub in subs:
        flags = _collect_flags(sub)
        for flag in flags:
            opt = flag.lstrip("-")
            if flag.startswith("--"):
                lines.append(
                    f"complete -c chimera -f -n '__fish_seen_subcommand_from {name}' -l '{opt}'"
                )
            else:
                lines.append(
                    f"complete -c chimera -f -n '__fish_seen_subcommand_from {name}' -s '{opt}'"
                )
    return "\n".join(lines) + "\n"


def generate_completion(
    parser: argparse.ArgumentParser,
    shell: str,
    cli_filter: str = "all",
) -> str:
    """Generate a completion script for the requested shell.

    Args:
        parser: The top-level chimera argparse parser.
        shell: One of ``"bash"``, ``"zsh"``, ``"fish"``.
        cli_filter: ``"all"`` or one of :data:`CODING_AGENT_CLIS`. When set
            to a specific CLI, only that subcommand (and ``completion``) are
            included in the output.

    Returns:
        The completion script as a single string, ready to write to stdout
        or append to a shell rc file.

    Raises:
        ValueError: If ``shell`` isn't one of the supported shells.
    """
    if shell not in ("bash", "zsh", "fish"):
        raise ValueError(
            f"Unsupported shell '{shell}'. Choose from: bash, zsh, fish."
        )
    pairs = list(_iter_subparsers(parser))
    pairs = _filter_subcommands(pairs, cli_filter)
    # Sort for deterministic output independent of registration order.
    pairs.sort(key=lambda p: p[0])
    top_flags = _collect_flags(parser)
    if shell == "bash":
        return _bash_script(top_flags, pairs)
    if shell == "zsh":
        return _zsh_script(top_flags, pairs)
    return _fish_script(top_flags, pairs)


#: Marker delimiters wrapping the source-line block in the user's rc file.
#: Re-running ``install`` looks for these to detect "already installed" and
#: ``--undo`` looks for them to remove the block. Picked to be unambiguous
#: and unlikely to collide with anything else a user might paste in.
MARKER_BEGIN = "# >>> chimera completion >>>"
MARKER_END = "# <<< chimera completion <<<"


def detect_shell(env_shell: str | None) -> str | None:
    """Detect the user's shell from a ``$SHELL``-style POSIX path.

    Args:
        env_shell: The value of ``$SHELL`` (or ``None`` if unset).

    Returns:
        ``"bash"``, ``"zsh"``, or ``"fish"`` if the basename matches; else
        ``None`` (caller decides whether to fall back or error).
    """
    if not env_shell:
        return None
    name = os.path.basename(env_shell.strip())
    if name.endswith("bash"):
        return "bash"
    if name.endswith("zsh"):
        return "zsh"
    if name.endswith("fish"):
        return "fish"
    return None


def _completion_paths(shell: str, home: Path) -> tuple[Path, Path | None]:
    """Return ``(script_path, rc_path)`` for the requested shell.

    ``rc_path`` is ``None`` for fish — fish autoloads completions from
    ``~/.config/fish/completions/`` so no rc edit is required.
    """
    if shell == "bash":
        return home / ".chimera" / "completion" / "bash.sh", home / ".bashrc"
    if shell == "zsh":
        return home / ".chimera" / "completion" / "zsh.sh", home / ".zshrc"
    if shell == "fish":
        return home / ".config" / "fish" / "completions" / "chimera.fish", None
    raise ValueError(f"Unsupported shell '{shell}'.")


def _build_marker_block(script_path: Path, shell: str) -> str:
    """Construct the rc-file marker block that sources the script.

    For bash and zsh this is a guarded ``source`` line. The block is
    bracketed by :data:`MARKER_BEGIN` / :data:`MARKER_END` so we can detect
    "already installed" and remove cleanly on ``--undo``.
    """
    # We deliberately use the explicit absolute path in the source line
    # rather than ``~`` expansion — bash/zsh both handle ``~`` only when
    # it's the first character of an unquoted word, and we want the line
    # to be portable across both shells without ambiguity.
    return (
        f"{MARKER_BEGIN}\n"
        f"# Generated by 'chimera completion install' ({shell})\n"
        f'[ -f "{script_path}" ] && source "{script_path}"\n'
        f"{MARKER_END}\n"
    )


def _strip_marker_block(text: str) -> str:
    """Return ``text`` with any chimera-completion marker block removed.

    Tolerates: trailing whitespace on marker lines, multiple blocks (only
    the first is removed — re-running ``--undo`` is idempotent), and
    missing trailing newline. Lines outside the block are preserved
    byte-for-byte.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_block = False
    for line in lines:
        stripped = line.rstrip()
        if not in_block and stripped == MARKER_BEGIN:
            in_block = True
            continue
        if in_block:
            if stripped == MARKER_END:
                in_block = False
            # Drop every line between the markers, including the markers.
            continue
        out.append(line)
    return "".join(out)


def _resolve_install_shell(
    requested: str,
    env_shell: str | None,
) -> tuple[str | None, str | None]:
    """Resolve the ``--shell`` flag against ``$SHELL``.

    Returns ``(shell, error)``. On success, ``shell`` is one of
    ``"bash"|"zsh"|"fish"`` and ``error`` is ``None``. On failure,
    ``shell`` is ``None`` and ``error`` is a friendly message.
    """
    if requested == "auto":
        detected = detect_shell(env_shell)
        if detected is None:
            return None, (
                "Could not auto-detect shell from $SHELL"
                f" ({env_shell!r}). Pass --shell bash|zsh|fish explicitly."
            )
        return detected, None
    if requested in ("bash", "zsh", "fish"):
        return requested, None
    return None, f"Unknown --shell value '{requested}'. Use auto|bash|zsh|fish."


_UNSET = object()  # sentinel: distinguish "explicit None" from "use default"


def install(
    *,
    shell: str = "auto",
    rc_path: str | None = None,
    undo: bool = False,
    dry_run: bool = False,
    home: Path | None = None,
    env_shell: object = _UNSET,
    out: "TextIO | None" = None,
) -> int:
    """Install (or uninstall) the chimera completion script for a shell.

    Args:
        shell: ``"auto"`` (default; detect from ``$SHELL``) or one of
            ``"bash" | "zsh" | "fish"``.
        rc_path: Override the rc-file path. When ``None``, defaults to
            ``~/.bashrc`` (bash), ``~/.zshrc`` (zsh), or unused (fish).
        undo: When ``True``, remove the marker block and delete the
            generated script file. Idempotent — calling twice is fine.
        dry_run: When ``True``, print the planned writes to ``out`` and
            return without touching disk.
        home: Override ``Path.home()``. Tests pass ``tmp_path`` here.
        env_shell: Override ``os.environ.get("SHELL")``. Tests use this
            to exercise the ``auto`` detection path.
        out: File-like for status messages. Defaults to ``sys.stdout``.

    Returns:
        ``0`` on success, ``1`` on auto-detect failure or invalid shell.
    """
    import sys
    from chimera.cli.main import build_parser

    home_path = home or Path.home()
    if env_shell is _UNSET:
        env: str | None = os.environ.get("SHELL")
    else:
        # Caller explicitly passed an env_shell value (including None for
        # "pretend $SHELL is unset"). Honor it verbatim.
        env = env_shell  # type: ignore[assignment]
    stream = out if out is not None else sys.stdout

    target, err = _resolve_install_shell(shell, env)
    if target is None:
        # Friendly error to stderr (still goes to ``stream`` if injected so
        # tests can capture it deterministically).
        print(f"chimera completion install: {err}", file=stream)
        return 1

    script_path, default_rc = _completion_paths(target, home_path)
    rc_file: Path | None
    if rc_path is not None:
        rc_file = Path(rc_path)
    else:
        rc_file = default_rc  # None for fish

    marker_block = _build_marker_block(script_path, target)

    if dry_run:
        action = "uninstall" if undo else "install"
        print(f"[dry-run] action={action} shell={target}", file=stream)
        print(f"[dry-run] script: {script_path}", file=stream)
        if rc_file is not None:
            print(f"[dry-run] rc:     {rc_file}", file=stream)
        else:
            print("[dry-run] rc:     (fish autoloads — no rc edit)", file=stream)
        return 0

    if undo:
        # Best-effort removal: missing files are not errors. We still
        # report what happened so users can confirm.
        if script_path.exists():
            script_path.unlink()
            print(f"removed: {script_path}", file=stream)
        else:
            print(f"not present: {script_path}", file=stream)
        if rc_file is not None and rc_file.exists():
            existing = rc_file.read_text()
            cleaned = _strip_marker_block(existing)
            if cleaned != existing:
                rc_file.write_text(cleaned)
                print(f"unwired: {rc_file}", file=stream)
            else:
                print(f"no marker block in: {rc_file}", file=stream)
        return 0

    # ---- normal install path ----
    parser = build_parser()
    script = generate_completion(parser, target, "all")

    # Make ~/.chimera/ tight (0o700) so other local users can't read or
    # tamper with shell scripts that get sourced on every login. The
    # fish path lives under ~/.config/fish/, which the user already
    # owns at whatever permission they've chosen — leave it alone.
    script_path.parent.mkdir(parents=True, exist_ok=True)
    if ".chimera" in script_path.parts:
        chimera_dir = home_path / ".chimera"
        try:
            chimera_dir.chmod(0o700)
        except OSError:
            # Non-fatal: some sandboxed test environments forbid chmod.
            pass
    script_path.write_text(script)
    print(f"wrote: {script_path}", file=stream)

    if rc_file is None:
        # fish: autoload, no rc edit needed.
        print(
            "fish autoloads completions from ~/.config/fish/completions/ —"
            " no rc edit needed.",
            file=stream,
        )
        return 0

    existing = rc_file.read_text() if rc_file.exists() else ""
    if MARKER_BEGIN in existing:
        # Idempotent: the marker block is already there, leave it alone.
        # (We don't try to "update" the line — if the user has edited the
        # block by hand we don't want to clobber that.)
        print(f"already wired: {rc_file}", file=stream)
        return 0

    # Append the marker block. Make sure we start on a new line so we
    # don't accidentally merge with whatever the user's rc file ended on.
    rc_file.parent.mkdir(parents=True, exist_ok=True)
    sep = "" if existing.endswith("\n") or existing == "" else "\n"
    rc_file.write_text(existing + sep + marker_block)
    print(f"wired: {rc_file}", file=stream)
    return 0


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the ``completion`` subcommand's arguments on ``parser``."""
    # The first positional accepts a shell name (legacy: print the script
    # to stdout) OR the ``install`` action. Keeping it as a single
    # positional preserves backward compatibility with the wave-9 surface
    # (``chimera completion bash``) without forcing users through an
    # extra subcommand level.
    parser.add_argument(
        "shell",
        choices=["bash", "zsh", "fish", "install"],
        help=(
            "Shell to generate completions for, OR 'install' to write the"
            " script and wire it into your shell's rc file."
        ),
    )
    parser.add_argument(
        "--cli",
        default="all",
        choices=["all", *CODING_AGENT_CLIS],
        help=(
            "Filter the completion script to a single coding-agent CLI. "
            "Default: 'all' (every chimera subcommand)."
        ),
    )
    # ---- install-mode flags (ignored when ``shell`` is bash|zsh|fish) ----
    parser.add_argument(
        "--shell",
        dest="target_shell",
        default="auto",
        choices=["auto", "bash", "zsh", "fish"],
        help=(
            "(install only) Which shell to install for. 'auto' (default)"
            " detects from $SHELL."
        ),
    )
    parser.add_argument(
        "--rc-path",
        dest="rc_path",
        default=None,
        help=(
            "(install only) Override the rc-file path. Defaults to"
            " ~/.bashrc or ~/.zshrc; unused for fish."
        ),
    )
    parser.add_argument(
        "--undo",
        action="store_true",
        help="(install only) Remove the marker block + delete the script file.",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="(install only) Print planned writes without touching disk.",
    )


def run(args: argparse.Namespace) -> int:
    """Execute the ``completion`` subcommand.

    Dispatches between two modes:

    - ``chimera completion bash|zsh|fish [...]`` — print the script to stdout
      (the wave-9 default behavior).
    - ``chimera completion install [...]`` — write the script + wire the rc
      file (the wave-11 addition).
    """
    if args.shell == "install":
        return install(
            shell=getattr(args, "target_shell", "auto"),
            rc_path=getattr(args, "rc_path", None),
            undo=getattr(args, "undo", False),
            dry_run=getattr(args, "dry_run", False),
        )
    # Local import to avoid a circular import at module load: main.py imports
    # this module while building the parser, and we need build_parser here to
    # walk the same parser tree.
    from chimera.cli.main import build_parser

    parser = build_parser()
    script = generate_completion(parser, args.shell, args.cli)
    print(script)
    return 0
