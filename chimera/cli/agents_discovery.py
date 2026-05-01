"""``chimera agents`` — top-level discovery command.

Lists all 7 coding-agent CLIs (mink, otter, ferret, weasel, shrew, stoat,
badger) with their purpose alias, a one-liner describing the design
posture, and the upstream tool that inspired each one.

Distinct from each CLI's own ``agents`` subcommand (e.g. ``chimera otter
agents list``), which lists agent *presets* within that CLI. The top-level
``chimera agents`` command (no further subcommand) is for picking which
CLI to use.
"""

from __future__ import annotations

import argparse
import dataclasses
import json


@dataclasses.dataclass(frozen=True)
class AgentEntry:
    """One row in the discovery catalogue."""

    codename: str
    alias: str
    inspired_by: str
    pitch: str


# WHY: hard-coded catalogue. The 7 codenames + aliases + inspirations are
# the stable, documented list. New CLIs added to chimera/cli/main.py
# should be appended here in the same order so ``chimera agents``
# output mirrors ``chimera --help`` discoverability.
_CATALOGUE: tuple[AgentEntry, ...] = (
    AgentEntry(
        codename="mink",
        alias="tui",
        inspired_by="Claude Code (Anthropic)",
        pitch="TUI-first interactive coding agent; ingests ~/.claude/settings.json",
    ),
    AgentEntry(
        codename="otter",
        alias="multi",
        inspired_by="opencode",
        pitch="Server-first, multi-client (HTTP+SSE+ACP); plugin-driven",
    ),
    AgentEntry(
        codename="ferret",
        alias="sandbox",
        inspired_by="codex (OpenAI)",
        pitch="Sandbox-first execution + IDE-flagship + OpenAI-default chain",
    ),
    AgentEntry(
        codename="weasel",
        alias="mini",
        inspired_by="pi (pi-mono)",
        pitch="Minimal harness, four operating modes (interactive/print/RPC/SDK)",
    ),
    AgentEntry(
        codename="shrew",
        alias="tiny",
        inspired_by="little-coder",
        pitch="Tuned for small local models (llama.cpp, Qwen MoE)",
    ),
    AgentEntry(
        codename="stoat",
        alias="shell",
        inspired_by="kimi-cli (Moonshot)",
        pitch="Shell-mode toggle (Ctrl-X); Kimi-tuned defaults",
    ),
    AgentEntry(
        codename="badger",
        alias="strict",
        inspired_by="claw-code",
        pitch="Harness-rewrite posture: tighter defaults, parity tracking, --rerun-on-failure",
    ),
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register ``chimera agents`` flags."""
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text table).",
    )


def _format_text() -> str:
    """Render the catalogue as an aligned text table."""
    lines: list[str] = []
    lines.append("chimera coding-agent CLIs:")
    lines.append("")
    # Compute column widths.
    cn_w = max(len("CODENAME"), max(len(e.codename) for e in _CATALOGUE))
    al_w = max(len("ALIAS"), max(len(e.alias) for e in _CATALOGUE))
    in_w = max(len("INSPIRED BY"), max(len(e.inspired_by) for e in _CATALOGUE))
    header = (
        f"  {'CODENAME':<{cn_w}}  {'ALIAS':<{al_w}}  "
        f"{'INSPIRED BY':<{in_w}}  PITCH"
    )
    lines.append(header)
    lines.append("  " + "-" * (cn_w + al_w + in_w + 6) + "  " + "-" * 5)
    for e in _CATALOGUE:
        lines.append(
            f"  {e.codename:<{cn_w}}  {e.alias:<{al_w}}  "
            f"{e.inspired_by:<{in_w}}  {e.pitch}"
        )
    lines.append("")
    lines.append(
        "Use either the codename or the alias: ``chimera mink`` ≡ ``chimera tui``."
    )
    lines.append(
        "See ``docs/inspirations.md`` for the full inspiration map and policy."
    )
    return "\n".join(lines)


def _format_json() -> str:
    """Render the catalogue as JSON."""
    return json.dumps(
        [dataclasses.asdict(e) for e in _CATALOGUE],
        indent=2,
    )


def run(args: argparse.Namespace) -> int:
    """Print the discovery catalogue."""
    fmt = getattr(args, "format", "text")
    if fmt == "json":
        print(_format_json())
    else:
        print(_format_text())
    return 0


__all__ = ["AgentEntry", "add_arguments", "run"]
