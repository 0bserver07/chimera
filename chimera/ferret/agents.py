"""``chimera ferret agents`` — discover and inspect agents.

Mirrors :mod:`chimera.otter.agents` but reads the ferret-flavored search
paths (``<project>/.codex/agent/*.md`` and ``~/.codex/agent/*.md``) on
top of the same built-in preset registry already shipped under
:mod:`chimera.agents.presets`. The point is that ``chimera ferret
--agent <name>`` and ``chimera ferret agents list`` walk the *same*
project > user > built-in chain, so users never have a name resolve in
one place and not the other.

Public surface:
    * :class:`FerretAgentRecord` — compact view of one discovered agent.
    * :func:`load_ferret_agents` — bulk loader returning :class:`AgentConfig`
      objects (consumed by callers wiring ferret through ``Agent.build``).
    * :func:`iter_agents` — discovery iterator yielding
      :class:`FerretAgentRecord` across all sources.
    * :func:`find_agent` — single-name resolver honoring project precedence.
    * :func:`format_agents_table` / :func:`format_agent_detail` — pretty
      printers used by the CLI subcommand handlers.
    * :func:`cmd_agents_list` / :func:`cmd_agents_show` — subcommand
      handlers wired by :mod:`chimera.ferret.cli`.

Trademark hygiene: this module never names the upstream coding agent
project. The ``~/.codex`` and ``.codex`` paths are filesystem facts (the
same way otter references ``.opencode``); see SPEC.md note on trademark
hygiene for details.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from chimera.agents.config import AgentConfig

__all__ = [
    "FERRET_PROJECT_AGENT_DIR",
    "FERRET_USER_AGENT_DIR",
    "FerretAgentRecord",
    "iter_agents",
    "find_agent",
    "load_ferret_agents",
    "format_agents_table",
    "format_agent_detail",
    "cmd_agents_list",
    "cmd_agents_show",
]


# ---------------------------------------------------------------------------
# Search paths
# ---------------------------------------------------------------------------

FERRET_PROJECT_AGENT_DIR = Path(".codex") / "agent"
"""Project-scope agent directory, relative to ``project_root``."""

FERRET_USER_AGENT_DIR = Path(".codex") / "agent"
"""User-scope agent directory, relative to ``Path.home()``."""

_BUILTIN_PRESETS: tuple[str, ...] = ("build", "plan", "review", "explore", "general")
"""Ferret ships these preset names — they alias the existing chimera presets.

We deliberately reuse :mod:`chimera.agents.presets` rather than parallel-
defining new configs: presets are codebase-wide assets and ferret being a
thin CLI flavor over chimera primitives is the whole point. Names match
the ones the upstream coding agent surfaces by default.
"""

_SOURCE_ORDER = {"project": 0, "user": 1, "builtin": 2}


# ---------------------------------------------------------------------------
# Record dataclass
# ---------------------------------------------------------------------------


@dataclass
class FerretAgentRecord:
    """Compact view of one discovered agent.

    Attributes:
        name: Agent name (matches ``--agent <name>``).
        source: ``"project"``, ``"user"``, or ``"builtin"``.
        model: Optional model override declared by the agent.
        tools: Declared tool names (may be empty — empty means default tools).
        description: One-line description (may be empty).
        path: Path to the source ``.md`` file; ``None`` for built-ins.
        system_prompt: Full markdown body used by :func:`format_agent_detail`.
    """

    name: str
    source: str
    model: str | None
    tools: list[str]
    description: str
    path: Path | None
    system_prompt: str = ""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _records_from_dir(directory: Path, source: str) -> Iterator[FerretAgentRecord]:
    """Yield :class:`FerretAgentRecord` for every ``*.md`` in ``directory``.

    Files that fail to parse are silently skipped so a single malformed
    agent never blocks the whole listing.
    """
    if not directory.is_dir():
        return
    from chimera.agents.loader import FileAgentDef

    for path in sorted(directory.glob("*.md")):
        try:
            fdef = FileAgentDef.from_file(path, source=source)
        except Exception:  # noqa: BLE001
            continue
        yield FerretAgentRecord(
            name=fdef.name,
            source=source,
            model=fdef.model,
            tools=list(fdef.tools),
            description=fdef.description,
            path=path,
            system_prompt=fdef.system_prompt,
        )


def _records_from_builtin() -> Iterator[FerretAgentRecord]:
    """Yield records for the built-in preset registry (build/explore/...).

    Uses :func:`chimera.agents.loader.create_default_registry` so the
    list always matches the registry consumed elsewhere in the codebase
    — drift between "what ferret shows" and "what chimera knows" would
    be the most confusing class of bug to chase.
    """
    try:
        from chimera.agents.loader import create_default_registry
    except Exception:  # noqa: BLE001
        return
    registry = create_default_registry()
    for name in registry.list():
        cfg = registry.get(name)
        if cfg is None:
            continue
        yield FerretAgentRecord(
            name=cfg.name,
            source="builtin",
            model=cfg.model,
            tools=list(cfg.tools),
            description=cfg.description,
            path=None,
            system_prompt=cfg.system_prompt,
        )


def iter_agents(cwd: Path | None = None) -> Iterator[FerretAgentRecord]:
    """Discover agents from project, user, and built-in sources.

    Args:
        cwd: Anchors the project-scope lookup. Defaults to :func:`Path.cwd`.

    Yields:
        :class:`FerretAgentRecord` instances in discovery order: project
        first, user second, built-in last. Duplicate names across
        sources are all yielded; :func:`find_agent` is the resolver.
    """
    cwd = cwd or Path.cwd()
    yield from _records_from_dir(cwd / FERRET_PROJECT_AGENT_DIR, "project")
    yield from _records_from_dir(Path.home() / FERRET_USER_AGENT_DIR, "user")
    yield from _records_from_builtin()


def find_agent(name: str, cwd: Path | None = None) -> FerretAgentRecord | None:
    """Resolve ``name`` to a single :class:`FerretAgentRecord` (project wins).

    Args:
        name: Agent name to resolve.
        cwd: Working directory used to anchor project-scope lookups.

    Returns:
        First matching record (project > user > builtin), or ``None``.
    """
    for record in iter_agents(cwd=cwd):
        if record.name == name:
            return record
    return None


# ---------------------------------------------------------------------------
# AgentConfig loader
# ---------------------------------------------------------------------------


def _agent_config_from_md(path: Path) -> AgentConfig | None:
    """Best-effort :class:`AgentConfig` from a markdown agent file.

    Returns ``None`` on parse failure rather than raising — a single
    malformed agent in the directory must not block the rest.
    """
    try:
        return AgentConfig.from_markdown(str(path))
    except Exception:  # noqa: BLE001
        return None


def load_ferret_agents(project_root: Path | None = None) -> list[AgentConfig]:
    """Load every ferret-discoverable agent as an :class:`AgentConfig`.

    Walks the same project > user > built-in chain :func:`iter_agents`
    uses, but yields :class:`AgentConfig` instances ready to be passed
    into :meth:`AgentConfig.build`. Later sources do *not* overwrite
    earlier ones — project takes precedence — to match the
    ``--agent <name>`` resolution path.

    Args:
        project_root: Project root used to anchor the project-scope
            search. Defaults to :func:`Path.cwd`.

    Returns:
        List of :class:`AgentConfig` instances in priority order
        (project entries first, then user-only, then built-in-only),
        with duplicates by ``name`` removed.
    """
    root = project_root or Path.cwd()
    seen: dict[str, AgentConfig] = {}

    # 1. Project — highest priority; ``.codex/agent/*.md``.
    for md_path in sorted((root / FERRET_PROJECT_AGENT_DIR).glob("*.md")) \
            if (root / FERRET_PROJECT_AGENT_DIR).is_dir() else []:
        cfg = _agent_config_from_md(md_path)
        if cfg is not None and cfg.name not in seen:
            seen[cfg.name] = cfg

    # 2. User — second priority; ``~/.codex/agent/*.md``.
    user_dir = Path.home() / FERRET_USER_AGENT_DIR
    if user_dir.is_dir():
        for md_path in sorted(user_dir.glob("*.md")):
            cfg = _agent_config_from_md(md_path)
            if cfg is not None and cfg.name not in seen:
                seen[cfg.name] = cfg

    # 3. Built-in presets — fallback. Pulled from the central registry
    #    so we never drift from the rest of chimera.
    try:
        from chimera.agents.loader import create_default_registry
    except Exception:  # noqa: BLE001
        return list(seen.values())

    registry = create_default_registry()
    for preset_name in _BUILTIN_PRESETS:
        if preset_name in seen:
            continue
        cfg = registry.get(preset_name)
        if cfg is not None:
            seen[preset_name] = cfg
    for extra_name in registry.list():
        if extra_name in seen:
            continue
        cfg = registry.get(extra_name)
        if cfg is not None:
            seen[extra_name] = cfg
    return list(seen.values())


# ---------------------------------------------------------------------------
# Formatting (mirrors chimera.otter.agents almost line-for-line so users
# get the same look across all three subcommands).
# ---------------------------------------------------------------------------


_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_RESET = "\x1b[0m"


def _color(text: str, code: str, *, enable: bool) -> str:
    """Wrap ``text`` in ANSI ``code`` when ``enable`` is True."""
    return f"{code}{text}{_RESET}" if enable else text


def _short(s: str, width: int) -> str:
    """Truncate ``s`` to ``width`` columns, replacing newlines with spaces."""
    flat = s.replace("\n", " ").replace("\r", " ")
    if len(flat) <= width:
        return flat
    if width <= 1:
        return flat[:width]
    return flat[: width - 1] + "…"


def format_agents_table(
    records: Iterable[FerretAgentRecord],
    no_color: bool = False,
) -> str:
    """Render ``records`` as a fixed-column ANSI-light table.

    Columns: ``NAME | SOURCE | MODEL | TOOLS | DESCRIPTION``. Sort:
    project > user > builtin, alphabetical within each. Description is
    truncated to 60 columns.

    Args:
        records: Iterable of :class:`FerretAgentRecord` to render.
        no_color: When True, suppress ANSI escapes regardless of TTY.

    Returns:
        Multi-line string. Empty input returns header + friendly footer.
    """
    enable = not no_color
    rows = sorted(
        records, key=lambda r: (_SOURCE_ORDER.get(r.source, 99), r.name.lower())
    )
    cols = (
        ("NAME", 24), ("SOURCE", 8), ("MODEL", 22),
        ("TOOLS", 28), ("DESCRIPTION", 60),
    )
    header = "  ".join(_color(n.ljust(w), _BOLD, enable=enable) for n, w in cols)
    if not rows:
        return f"{header}\n{_color('(no agents discovered)', _DIM, enable=enable)}"
    lines = [header]
    for r in rows:
        tools_str = ",".join(r.tools) if r.tools else "-"
        model_str = r.model or "-"
        lines.append("  ".join([
            _short(r.name, 24).ljust(24),
            r.source.ljust(8),
            _short(model_str, 22).ljust(22),
            _short(tools_str, 28).ljust(28),
            _short(r.description, 60),
        ]))
    return "\n".join(lines)


def format_agent_detail(record: FerretAgentRecord, no_color: bool = False) -> str:
    """Pretty-print one agent: header, metadata, full tools, body preview.

    Body preview is the first 20 lines of ``record.system_prompt`` with a
    trailing ``... (N more lines)`` marker when the body is longer.

    Args:
        record: The :class:`FerretAgentRecord` to render.
        no_color: When True, suppress ANSI escapes.

    Returns:
        Multi-line string suitable for direct ``print``.
    """
    enable = not no_color
    out: list[str] = []
    out.append(_color(f"Agent: {record.name}", _BOLD, enable=enable))
    out.append(f"  source:      {record.source}")
    out.append(f"  path:        {record.path if record.path else '(built-in)'}")
    out.append(f"  model:       {record.model or '(none — uses --model)'}")
    tools_str = ", ".join(record.tools) if record.tools else "(none — all default tools)"
    out.append(f"  tools:       {tools_str}")
    out.append("")
    out.append(_color("Description:", _BOLD, enable=enable))
    desc = record.description or "(no description)"
    for line in desc.splitlines() or [""]:
        out.append(f"  {line}")
    out.append("")
    out.append(_color("System prompt (first 20 lines):", _BOLD, enable=enable))
    body = record.system_prompt or ""
    if not body.strip():
        out.append(_color("  (empty)", _DIM, enable=enable))
        return "\n".join(out)
    body_lines = body.splitlines()
    for line in body_lines[:20]:
        out.append(f"  {line}")
    if len(body_lines) > 20:
        out.append(_color(f"  ... ({len(body_lines) - 20} more lines)", _DIM, enable=enable))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Subcommand handlers — wired by chimera.ferret.cli's _dispatch_agents
# ---------------------------------------------------------------------------


def cmd_agents_list(*, no_color: bool = False, cwd: Path | None = None) -> int:
    """Implement ``chimera ferret agents list``.

    Args:
        no_color: When True, suppress ANSI escapes regardless of TTY.
        cwd: Project root anchor. Defaults to :func:`Path.cwd`.

    Returns:
        Exit code: ``0`` on success.
    """
    records = list(iter_agents(cwd=cwd))
    print(format_agents_table(records, no_color=no_color))
    return 0


def cmd_agents_show(
    name: str | None,
    *,
    no_color: bool = False,
    cwd: Path | None = None,
) -> int:
    """Implement ``chimera ferret agents show <name>``.

    Args:
        name: Agent name to show. ``None`` returns exit 2 with a usage hint.
        no_color: When True, suppress ANSI color.
        cwd: Project root anchor. Defaults to :func:`Path.cwd`.

    Returns:
        Exit code: ``0`` on success, ``2`` when the name is missing or
        unresolved.
    """
    import sys

    if not name:
        print(
            "error: 'ferret agents show' requires an AGENT_NAME argument "
            "(see 'ferret agents list' for available names).",
            file=sys.stderr,
        )
        return 2

    record = find_agent(name, cwd=cwd)
    if record is None:
        builtins = ", ".join(_BUILTIN_PRESETS)
        print(
            f"error: agent '{name}' not found in .codex/agent/, "
            "~/.codex/agent/, or built-in registry. "
            f"Built-in presets: {builtins}.",
            file=sys.stderr,
        )
        return 2

    print(format_agent_detail(record, no_color=no_color))
    return 0
