"""``chimera mink agents`` — list and inspect available agents.

Surfaces every agent reachable from the same project > user > built-in
chain ``--agent <name>`` walks (see
:func:`chimera.mink.cli._resolve_agent_spec`), so listing and resolution
always agree about which name maps to which definition.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

__all__ = [
    "AgentRecord",
    "iter_agents",
    "find_agent",
    "format_agents_table",
    "format_agent_detail",
]


@dataclass
class AgentRecord:
    """Compact view of one discovered agent.

    Attributes:
        name: Agent name (matches ``--agent <name>``).
        source: ``"project"``, ``"user"``, or ``"builtin"``.
        model: Optional model override declared by the agent.
        tools: Declared tool names (may be empty).
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


_SOURCE_ORDER = {"project": 0, "user": 1, "builtin": 2}


def _records_from_dir(directory: Path, source: str) -> Iterator[AgentRecord]:
    """Yield :class:`AgentRecord` for every ``*.md`` in ``directory``.

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
        yield AgentRecord(
            name=fdef.name,
            source=source,
            model=fdef.model,
            tools=list(fdef.tools),
            description=fdef.description,
            path=path,
            system_prompt=fdef.system_prompt,
        )


def _records_from_builtin() -> Iterator[AgentRecord]:
    """Yield records for the built-in preset registry (build/explore/...)."""
    try:
        from chimera.agents.loader import create_default_registry
    except Exception:  # noqa: BLE001
        return
    registry = create_default_registry()
    for name in registry.list():
        cfg = registry.get(name)
        if cfg is None:
            continue
        yield AgentRecord(
            name=cfg.name,
            source="builtin",
            model=cfg.model,
            tools=list(cfg.tools),
            description=cfg.description,
            path=None,
            system_prompt=cfg.system_prompt,
        )


def iter_agents(cwd: Path | None = None) -> Iterator[AgentRecord]:
    """Discover agents from project, user, and built-in sources.

    Args:
        cwd: Anchors the project-scope lookup. Defaults to :func:`Path.cwd`.

    Yields:
        :class:`AgentRecord` instances in discovery order. Duplicate names
        across sources are all yielded; :func:`find_agent` is the resolver.
    """
    cwd = cwd or Path.cwd()
    yield from _records_from_dir(cwd / ".claude" / "agents", "project")
    yield from _records_from_dir(Path.home() / ".claude" / "agents", "user")
    yield from _records_from_builtin()


def find_agent(name: str, cwd: Path | None = None) -> AgentRecord | None:
    """Resolve ``name`` to a single :class:`AgentRecord` (project wins).

    Mirrors :func:`chimera.mink.cli._resolve_agent_spec`'s priority chain.

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
# Formatting
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
    records: Iterable[AgentRecord],
    no_color: bool = False,
) -> str:
    """Render ``records`` as a fixed-column ANSI-light table.

    Columns: ``NAME | SOURCE | MODEL | TOOLS | DESCRIPTION``. Sort:
    project > user > builtin, alphabetical within each. Description is
    truncated to 60 columns.

    Args:
        records: Iterable of :class:`AgentRecord` to render.
        no_color: When True, suppress ANSI escapes regardless of TTY.

    Returns:
        Multi-line string. Empty input returns header + friendly footer.
    """
    enable = not no_color
    rows = sorted(records, key=lambda r: (_SOURCE_ORDER.get(r.source, 99), r.name.lower()))
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


def format_agent_detail(record: AgentRecord, no_color: bool = False) -> str:
    """Pretty-print one agent: header, metadata, full tools, body preview.

    Body preview is the first 20 lines of ``record.system_prompt`` with a
    trailing ``... (N more lines)`` marker when the body is longer.

    Args:
        record: The :class:`AgentRecord` to render.
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
