"""Shell completion script generation for the ``chimera`` CLI.

Generates dynamic completion scripts for bash, zsh, and fish by walking the
top-level argparse parser. Users source the output into their shell config::

    chimera completion bash >> ~/.bashrc
    chimera completion zsh  >> ~/.zshrc
    chimera completion fish >  ~/.config/fish/completions/chimera.fish

Stdlib-only. Discovery is structural — when a new subcommand is added to
``build_parser`` it shows up in the generated completions automatically.

The optional ``--cli`` filter narrows the surface to a single coding-agent
sub-CLI (mink/otter/ferret/weasel/shrew/stoat/badger) so users who only ever
invoke one agent can keep their completion table small.
"""
from __future__ import annotations

import argparse
from typing import Iterable

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


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the ``completion`` subcommand's arguments on ``parser``."""
    parser.add_argument(
        "shell",
        choices=["bash", "zsh", "fish"],
        help="Shell to generate completions for.",
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


def run(args: argparse.Namespace) -> int:
    """Execute the ``completion`` subcommand. Writes the script to stdout."""
    # Local import to avoid a circular import at module load: main.py imports
    # this module while building the parser, and we need build_parser here to
    # walk the same parser tree.
    from chimera.cli.main import build_parser

    parser = build_parser()
    script = generate_completion(parser, args.shell, args.cli)
    print(script)
    return 0
