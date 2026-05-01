"""``chimera otter agents`` — discover and inspect agents.

Mirrors :mod:`chimera.mink.agents` but reads the otter-flavored search
paths (``<project>/.opencode/agent/*.md`` and ``~/.opencode/agent/*.md``)
on top of the same built-in preset registry already shipped under
:mod:`chimera.agents.presets`. The point is that ``chimera otter --agent
<name>`` and ``chimera otter agents list`` walk the *same* project >
user > built-in chain, so users never have a name resolve in one place
and not the other.

Public surface:
    * :class:`OtterAgentRecord` — compact view of one discovered agent.
    * :func:`load_otter_agents` — bulk loader returning :class:`AgentConfig`
      objects (consumed by callers wiring otter through ``Agent.build``).
    * :func:`iter_agents` — discovery iterator yielding :class:`OtterAgentRecord`
      across all sources.
    * :func:`find_agent` — single-name resolver honoring project precedence.
    * :func:`format_agents_table` / :func:`format_agent_detail` — pretty
      printers used by the CLI subcommand handlers.
    * :func:`cmd_agents_list` / :func:`cmd_agents_show` — subcommand
      handlers wired by :mod:`chimera.otter.cli`.

Trademark hygiene: this module never names the upstream coding agent
project. The ``~/.opencode`` and ``.opencode`` paths are filesystem
facts (the same way mink references ``~/.claude``); see SPEC.md note on
trademark hygiene for details.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from chimera.agents.config import AgentConfig

__all__ = [
    "OTTER_PROJECT_AGENT_DIR",
    "OTTER_USER_AGENT_DIR",
    "OtterAgentRecord",
    "iter_agents",
    "find_agent",
    "load_otter_agents",
    "format_agents_table",
    "format_agent_detail",
    "cmd_agents_list",
    "cmd_agents_show",
    "cmd_agents_create",
]


# ---------------------------------------------------------------------------
# Search paths
# ---------------------------------------------------------------------------

OTTER_PROJECT_AGENT_DIR = Path(".opencode") / "agent"
"""Project-scope agent directory, relative to ``project_root``."""

OTTER_USER_AGENT_DIR = Path(".opencode") / "agent"
"""User-scope agent directory, relative to ``Path.home()``."""

_BUILTIN_PRESETS: tuple[str, ...] = ("build", "plan", "review", "explore", "general")
"""Otter ships these preset names — they alias the existing chimera presets.

We deliberately reuse :mod:`chimera.agents.presets` rather than parallel-
defining new configs: presets are codebase-wide assets and otter being a
thin CLI flavor over chimera primitives is the whole point. Names match
the ones the upstream coding agent surfaces by default.
"""

_SOURCE_ORDER = {"project": 0, "user": 1, "builtin": 2}


# ---------------------------------------------------------------------------
# Record dataclass
# ---------------------------------------------------------------------------


@dataclass
class OtterAgentRecord:
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


def _records_from_dir(directory: Path, source: str) -> Iterator[OtterAgentRecord]:
    """Yield :class:`OtterAgentRecord` for every ``*.md`` in ``directory``.

    Files that fail to parse are silently skipped so a single malformed
    agent never blocks the whole listing. Mirrors the same defensive
    posture used by :mod:`chimera.mink.agents`.
    """
    if not directory.is_dir():
        return
    # Lazy import — :class:`FileAgentDef` lives in :mod:`chimera.agents.loader`
    # and pulling it at module-import time would tug in the rest of the
    # agent loader chain even for callers that just want preset names.
    from chimera.agents.loader import FileAgentDef

    for path in sorted(directory.glob("*.md")):
        try:
            fdef = FileAgentDef.from_file(path, source=source)
        except Exception:  # noqa: BLE001
            continue
        yield OtterAgentRecord(
            name=fdef.name,
            source=source,
            model=fdef.model,
            tools=list(fdef.tools),
            description=fdef.description,
            path=path,
            system_prompt=fdef.system_prompt,
        )


def _records_from_builtin() -> Iterator[OtterAgentRecord]:
    """Yield records for the built-in preset registry (build/explore/...).

    Uses :func:`chimera.agents.loader.create_default_registry` so the
    list always matches the registry consumed elsewhere in the codebase
    — drift between "what otter shows" and "what chimera knows" would
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
        yield OtterAgentRecord(
            name=cfg.name,
            source="builtin",
            model=cfg.model,
            tools=list(cfg.tools),
            description=cfg.description,
            path=None,
            system_prompt=cfg.system_prompt,
        )


def iter_agents(cwd: Path | None = None) -> Iterator[OtterAgentRecord]:
    """Discover agents from project, user, and built-in sources.

    Args:
        cwd: Anchors the project-scope lookup. Defaults to :func:`Path.cwd`.

    Yields:
        :class:`OtterAgentRecord` instances in discovery order: project
        first, user second, built-in last. Duplicate names across
        sources are all yielded; :func:`find_agent` is the resolver.
    """
    cwd = cwd or Path.cwd()
    yield from _records_from_dir(cwd / OTTER_PROJECT_AGENT_DIR, "project")
    yield from _records_from_dir(Path.home() / OTTER_USER_AGENT_DIR, "user")
    yield from _records_from_builtin()


def find_agent(name: str, cwd: Path | None = None) -> OtterAgentRecord | None:
    """Resolve ``name`` to a single :class:`OtterAgentRecord` (project wins).

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
# AgentConfig loader (consumed by callers wiring an Agent through
# AgentConfig.build); built-in presets included so a single call surfaces
# every agent reachable from a given project root.
# ---------------------------------------------------------------------------


def _agent_config_from_md(path: Path) -> AgentConfig | None:
    """Best-effort :class:`AgentConfig` from a markdown agent file.

    Returns ``None`` on parse failure rather than raising — a single
    malformed agent in the directory must not block the rest. We delegate
    to :meth:`AgentConfig.from_markdown` so the schema (frontmatter
    keys, body parsing) stays single-sourced.
    """
    try:
        return AgentConfig.from_markdown(str(path))
    except Exception:  # noqa: BLE001
        return None


def load_otter_agents(project_root: Path | None = None) -> list[AgentConfig]:
    """Load every otter-discoverable agent as an :class:`AgentConfig`.

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

    # 1. Project — highest priority; ``.opencode/agent/*.md``.
    for md_path in sorted((root / OTTER_PROJECT_AGENT_DIR).glob("*.md")) \
            if (root / OTTER_PROJECT_AGENT_DIR).is_dir() else []:
        cfg = _agent_config_from_md(md_path)
        if cfg is not None and cfg.name not in seen:
            seen[cfg.name] = cfg

    # 2. User — second priority; ``~/.opencode/agent/*.md``.
    user_dir = Path.home() / OTTER_USER_AGENT_DIR
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
    # Also include any extras the registry exposes that we didn't enumerate.
    for extra_name in registry.list():
        if extra_name in seen:
            continue
        cfg = registry.get(extra_name)
        if cfg is not None:
            seen[extra_name] = cfg
    return list(seen.values())


# ---------------------------------------------------------------------------
# Formatting (mirrors chimera.mink.agents almost line-for-line so users
# get the same look across both subcommands).
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
    records: Iterable[OtterAgentRecord],
    no_color: bool = False,
) -> str:
    """Render ``records`` as a fixed-column ANSI-light table.

    Columns: ``NAME | SOURCE | MODEL | TOOLS | DESCRIPTION``. Sort:
    project > user > builtin, alphabetical within each. Description is
    truncated to 60 columns.

    Args:
        records: Iterable of :class:`OtterAgentRecord` to render.
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


def format_agent_detail(record: OtterAgentRecord, no_color: bool = False) -> str:
    """Pretty-print one agent: header, metadata, full tools, body preview.

    Body preview is the first 20 lines of ``record.system_prompt`` with a
    trailing ``... (N more lines)`` marker when the body is longer.

    Args:
        record: The :class:`OtterAgentRecord` to render.
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
# Subcommand handlers — wired by chimera.otter.cli's _dispatch_agents
# ---------------------------------------------------------------------------


def cmd_agents_list(*, no_color: bool = False, cwd: Path | None = None) -> int:
    """Implement ``chimera otter agents list``.

    Args:
        no_color: When True, suppress ANSI escapes regardless of TTY.
        cwd: Project root anchor. Defaults to :func:`Path.cwd`.

    Returns:
        Exit code: ``0`` on success (always — an empty result is still
        a valid answer, matching :mod:`chimera.mink.agents` behavior).
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
    """Implement ``chimera otter agents show <name>``.

    Args:
        name: Agent name to show. ``None`` returns exit 2 with a usage hint.
        no_color: When True, suppress ANSI color.
        cwd: Project root anchor. Defaults to :func:`Path.cwd`.

    Returns:
        Exit code: ``0`` on success, ``2`` when the name is missing or
        unresolved.
    """
    if not name:
        print(
            "error: 'otter agents show' requires an AGENT_NAME argument "
            "(see 'otter agents list' for available names).",
            file=sys.stderr,
        )
        return 2

    record = find_agent(name, cwd=cwd)
    if record is None:
        builtins = ", ".join(_BUILTIN_PRESETS)
        print(
            f"error: agent '{name}' not found in .opencode/agent/, "
            "~/.opencode/agent/, or built-in registry. "
            f"Built-in presets: {builtins}.",
            file=sys.stderr,
        )
        return 2

    print(format_agent_detail(record, no_color=no_color))
    return 0


# ---------------------------------------------------------------------------
# Interactive scaffolder — ``otter agents create``
# ---------------------------------------------------------------------------


_DEFAULT_MODEL_CHAIN = "anthropic/claude-sonnet-4-6"
"""Default ``model:`` value suggested when the user accepts the chain.

WHY: keeps a concrete, working default in the prompt so users hitting
``Enter`` don't end up with an agent file that crashes provider routing.
The string mirrors the ``provider/model`` form documented in
``docs/otter/agents.md``.
"""


def _agent_tool_names() -> list[str]:
    """Return the canonical tool names available to a markdown agent.

    Wrapper around :data:`chimera.core.tool_group.AGENT_TOOLS` so the
    interactive scaffolder doesn't import the heavy tool registry at
    module-import time.

    Returns:
        Sorted list of tool names (e.g. ``["bash", "edit_file", ...]``).
    """
    from chimera.core.tool_group import AGENT_TOOLS

    return sorted(t.name for t in AGENT_TOOLS)


def _resolve_create_target(
    name: str,
    *,
    user: bool,
    cwd: Path,
) -> Path:
    """Resolve the destination ``.md`` path for a new otter agent.

    Args:
        name: Agent name (becomes ``<name>.md``).
        user: When ``True``, resolve against ``~/.opencode/agent/``;
            otherwise project scope (``<cwd>/.opencode/agent/``).
        cwd: Project root anchor (only used in project scope).

    Returns:
        Absolute path to ``<scope>/.opencode/agent/<name>.md``.
    """
    base = (Path.home() / OTTER_USER_AGENT_DIR) if user else (cwd / OTTER_PROJECT_AGENT_DIR)
    return base / f"{name}.md"


def _prompt(
    message: str,
    *,
    default: str | None = None,
    input_fn: Any = None,
) -> str:
    """Prompt for a single line of input with optional default.

    Args:
        message: Prompt label (no trailing colon required).
        default: Value returned when the user submits an empty line.
            ``None`` means "no default" (empty input returns ``""``).
        input_fn: Override for stdlib :func:`input` (used by tests).
            ``None`` resolves to :func:`builtins.input` at call time so
            ``monkeypatch.setattr("builtins.input", ...)`` works.

    Returns:
        The user's input, or ``default`` when input is empty/whitespace.
    """
    if input_fn is None:
        import builtins
        input_fn = builtins.input
    suffix = f" [{default}]" if default else ""
    raw = input_fn(f"{message}{suffix}: ")
    text = (raw or "").strip()
    if not text and default is not None:
        return default
    return text


def _prompt_multiline(
    message: str,
    *,
    sentinel: str = ".",
    input_fn: Any = None,
) -> str:
    """Prompt for a multi-line block terminated by ``sentinel`` on its own line.

    Mirrors the ``mailx`` / ``ed`` convention so the scaffolder stays
    stdlib-only (no readline-multiline gymnastics, no editor spawn).

    Args:
        message: Prompt label printed once before the block.
        sentinel: Single-character line that terminates the block. The
            sentinel line itself is NOT included in the returned text.
        input_fn: Override for stdlib :func:`input` (used by tests).
            ``None`` resolves to :func:`builtins.input` at call time.

    Returns:
        The collected lines joined by ``"\\n"``. May be empty.
    """
    if input_fn is None:
        import builtins
        input_fn = builtins.input
    print(f"{message}")
    print(f"  (end with a single '{sentinel}' on its own line)")
    lines: list[str] = []
    while True:
        try:
            line = input_fn("  ")
        except EOFError:
            break
        if line.strip() == sentinel:
            break
        lines.append(line)
    return "\n".join(lines)


def _prompt_tools(
    available: list[str],
    *,
    input_fn: Any = None,
) -> list[str]:
    """Prompt for a comma-separated tool list, validated against ``available``.

    Empty input returns ``[]`` which means "inherit AGENT_TOOLS at build
    time" (matches :class:`AgentConfig` semantics).

    Args:
        available: The full list of valid tool names.
        input_fn: Override for stdlib :func:`input` (used by tests).
            ``None`` resolves to :func:`builtins.input` at call time.

    Returns:
        Validated list of tool names. Unknown names are dropped with a
        warning printed to stdout.
    """
    if input_fn is None:
        import builtins
        input_fn = builtins.input
    print("Available tools: " + ", ".join(available))
    raw = input_fn(
        "Tools (comma-separated; empty = all default tools): "
    )
    text = (raw or "").strip()
    if not text:
        return []
    wanted = [n.strip() for n in text.split(",") if n.strip()]
    valid = []
    valid_set = set(available)
    for name in wanted:
        if name in valid_set:
            valid.append(name)
        else:
            print(f"  (skipping unknown tool: {name!r})")
    return valid


def _render_agent_md(
    *,
    name: str,
    description: str,
    model: str,
    tools: list[str],
    system_prompt: str,
) -> str:
    """Render the markdown body for a new otter agent file.

    The output is the canonical schema consumed by
    :meth:`AgentConfig.from_markdown`: a YAML-ish frontmatter block
    delimited by ``---`` markers, followed by the system prompt body.

    Args:
        name: Agent name (frontmatter ``name:``).
        description: One-line description (frontmatter ``description:``).
        model: Model identifier (frontmatter ``model:``); empty string
            omits the key entirely.
        tools: Tool names; empty list omits the key entirely.
        system_prompt: Markdown body appended after the closing ``---``.

    Returns:
        The full markdown string ready to write to disk.
    """
    lines = ["---", f"name: {name}"]
    if description:
        # WHY: quote the description so a trailing colon or comma in user
        # input doesn't accidentally break the frontmatter parser.
        safe = description.replace('"', '\\"')
        lines.append(f'description: "{safe}"')
    if model:
        lines.append(f"model: {model}")
    if tools:
        lines.append("tools: [" + ", ".join(tools) + "]")
    lines.append("---")
    lines.append("")
    body = system_prompt.strip()
    if body:
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


def cmd_agents_create(
    name: str | None,
    *,
    user: bool = False,
    cwd: Path | None = None,
    input_fn: Any = None,
    confirm: bool = True,
) -> int:
    """Implement ``chimera otter agents create [<name>]``.

    Interactive scaffolder that prompts for the standard agent fields
    (name, description, model, tools, system prompt) and writes the
    resulting markdown to ``<scope>/.opencode/agent/<name>.md``. Defaults
    to project scope; pass ``user=True`` for ``~/.opencode/agent/``.

    The function is stdlib-only — no curses, no readline gymnastics —
    so tests can drive it via ``monkeypatch.setattr(builtins.input, ...)``.

    Args:
        name: Pre-filled agent name from the CLI. ``None`` prompts.
        user: When ``True``, write to user scope instead of project.
        cwd: Project root anchor for project scope. Defaults to
            :func:`Path.cwd`.
        input_fn: Override for stdlib :func:`input` (used by tests).
        confirm: When ``True``, ask for ``y/n`` before writing the file.
            Tests pass ``confirm=False`` to skip the final gate.

    Returns:
        Exit code: ``0`` on success, ``1`` on user abort, ``2`` on
        validation error (empty name, target already exists).
    """
    cwd = cwd or Path.cwd()
    if input_fn is None:
        # Resolve at call time so monkeypatch.setattr("builtins.input", ...)
        # in tests reaches the prompt path. (Default-bound argument captures
        # the original ``input`` reference at function-definition time and
        # short-circuits the patch.)
        import builtins
        input_fn = builtins.input

    # 1. Name — required. Use the CLI-provided value as default if any.
    resolved_name = _prompt(
        "Agent name", default=name, input_fn=input_fn,
    )
    if not resolved_name:
        print("error: agent name is required.", file=sys.stderr)
        return 2
    # Sanity-check: bare slug-ish names only. The frontmatter ``name:``
    # parser is permissive but downstream registries key on this string.
    if any(ch in resolved_name for ch in ("/", "\\", " ", "\t", "\n")):
        print(
            f"error: agent name {resolved_name!r} contains invalid "
            "characters (no spaces or path separators).",
            file=sys.stderr,
        )
        return 2

    # 2. Resolve target path early so we can fail fast on collision.
    target = _resolve_create_target(resolved_name, user=user, cwd=cwd)
    if target.exists():
        print(
            f"error: {target} already exists; refusing to overwrite. "
            "Edit the file in-place or pick a different name.",
            file=sys.stderr,
        )
        return 2

    # 3. Description.
    description = _prompt(
        "Description", default="", input_fn=input_fn,
    )

    # 4. Model — default to the documented chain so Enter-through gives
    #    a working agent.
    model = _prompt(
        "Model",
        default=_DEFAULT_MODEL_CHAIN,
        input_fn=input_fn,
    )

    # 5. Tools — multi-select-ish via comma list, validated against
    #    AGENT_TOOLS.
    tools = _prompt_tools(_agent_tool_names(), input_fn=input_fn)

    # 6. System prompt — multi-line block.
    system_prompt = _prompt_multiline(
        "System prompt", input_fn=input_fn,
    )

    # 7. Render + confirm.
    rendered = _render_agent_md(
        name=resolved_name,
        description=description,
        model=model,
        tools=tools,
        system_prompt=system_prompt,
    )
    print("")
    print(f"About to write {target}:")
    print("---")
    print(rendered)
    print("---")
    if confirm:
        answer = input_fn("Write this file? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("aborted; no file written.", file=sys.stderr)
            return 1

    # 8. Write.
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    print(f"wrote {target}")
    return 0
