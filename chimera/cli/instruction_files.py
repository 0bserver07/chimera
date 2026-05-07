"""Shared loader for project-root instruction files.

Both Codex (`AGENTS.md`) and the upstream (`CLAUDE.md`) ingest a
project-root markdown file as system instructions. This module unifies
that discovery so every CLI surface (`mink`, `ferret`, future
front-ends) reaches the *same* set of files in the *same* order.

Discovery order (root-most first, leaf-most last) — child layers take
precedence on heading conflicts but are otherwise additive:

1. ``~/.codex/AGENTS.md`` (Codex user-global).
2. ``~/.opencode/AGENTS.md`` and ``~/.config/opencode/AGENTS.md``
   (OpenCode user-global; mirrors otter/rules).
3. ``~/.claude/CLAUDE.md`` (Claude Code user-global).
4. Project-root `AGENTS.md` walk-up (handled by
   :func:`chimera.config.project_discovery.discover_agents_docs` —
   already child-overrides-parent merged).
5. Project-root `CLAUDE.md` walk-up (handled by
   :func:`chimera.context.agent_memory.discover_memory_files`).
6. Project-root ``.chimera/rules.md`` if present (Chimera-native).

Use :func:`load_instruction_files` to get the ordered list of paths
and :func:`load_instruction_text` to get a single concatenated string
ready for inclusion in a system prompt.

Files larger than :data:`_MAX_FILE_BYTES` are read up to that cap and
flagged with a truncation marker; the caller may surface a warning.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "InstructionFile",
    "load_instruction_files",
    "load_instruction_text",
]

# 256 KiB per file — generous enough for any reasonable AGENTS.md /
# CLAUDE.md, small enough that an unbounded one doesn't blow up the
# system prompt.
_MAX_FILE_BYTES: int = 256 * 1024


@dataclass
class InstructionFile:
    """A single discovered instruction file.

    Attributes:
        path: Resolved absolute path to the file.
        text: File contents (truncated at :data:`_MAX_FILE_BYTES`).
        truncated: ``True`` if the file exceeded the read cap.
        source: Origin label used in the rendered prompt
            (``"AGENTS.md"`` / ``"CLAUDE.md"`` / ``"rules.md"``).
    """

    path: Path
    text: str
    truncated: bool
    source: str


def _read_capped(p: Path, source: str) -> InstructionFile | None:
    """Read *p* up to ``_MAX_FILE_BYTES``; return ``None`` on missing/unreadable."""
    try:
        if not p.is_file():
            return None
        raw = p.read_bytes()
    except OSError:
        return None
    truncated = len(raw) > _MAX_FILE_BYTES
    if truncated:
        raw = raw[:_MAX_FILE_BYTES]
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover — replace shouldn't raise
        return None
    return InstructionFile(
        path=p.resolve(), text=text, truncated=truncated, source=source,
    )


def _user_level_paths() -> list[tuple[Path, str]]:
    home = Path.home()
    return [
        (home / ".codex" / "AGENTS.md", "AGENTS.md"),
        (home / ".opencode" / "AGENTS.md", "AGENTS.md"),
        (home / ".config" / "opencode" / "AGENTS.md", "AGENTS.md"),
        (home / ".claude" / "CLAUDE.md", "CLAUDE.md"),
    ]


def load_instruction_files(
    project_dir: Path | str | None = None,
) -> list[InstructionFile]:
    """Return all discovered instruction files in injection order.

    Late-binds the heavy walk-up modules (``project_discovery`` and
    ``agent_memory``) so importing this module stays cheap and keeps
    the no-files case free of side effects.

    Side effect: fires :data:`HookEvent.INSTRUCTIONS_LOADED` once with
    the list of resolved paths so observers (audit log, agent telemetry)
    can record which files shaped the system prompt. Empty discovery
    skips the emit; errors inside the emit path are swallowed.

    Args:
        project_dir: Project root anchor. Defaults to ``Path.cwd()``.

    Returns:
        Ordered list of :class:`InstructionFile`. May be empty.
    """
    out: list[InstructionFile] = []
    seen: set[Path] = set()

    def _push(p: Path, source: str) -> None:
        rp = p.resolve()
        if rp in seen:
            return
        f = _read_capped(rp, source)
        if f is None:
            return
        seen.add(rp)
        out.append(f)

    # 1. User-level instruction files (root-most layer).
    for upath, source in _user_level_paths():
        _push(upath, source)

    cwd = Path(project_dir).resolve() if project_dir is not None else Path.cwd().resolve()

    # 2. Project AGENTS.md walk-up (Codex hierarchy).
    try:
        from chimera.config.project_discovery import discover_agents_docs

        doc = discover_agents_docs(cwd)
        if doc is not None:
            for sp in doc.source_paths:
                _push(Path(sp), "AGENTS.md")
    except Exception:
        # Discovery is best-effort; never block CLI startup.
        pass

    # 3. CLAUDE.md walk-up (CC hierarchy).
    try:
        from chimera.context.agent_memory import discover_memory_files

        for mp in discover_memory_files(cwd=cwd):
            _push(mp, "CLAUDE.md")
    except Exception:
        pass

    # 4. Chimera-native rules file.
    chimera_rules = cwd / ".chimera" / "rules.md"
    _push(chimera_rules, "rules.md")

    if out:
        _emit_instructions_loaded(out)

    return out


def _emit_instructions_loaded(files: list[InstructionFile]) -> None:
    """Fire :data:`HookEvent.INSTRUCTIONS_LOADED` after a successful walk.

    Best-effort: a missing global emitter or an in-hook exception is
    swallowed so CLI startup never crashes on observability code.

    The emit carries ``tool_input`` with the resolved paths and source
    labels so a hook can render an accurate "instructions seeded" banner
    or audit which CLAUDE.md / AGENTS.md files shaped the prompt.
    """
    try:
        from chimera.hooks.emitter import get_global_emitter
        from chimera.hooks.events import HookEvent

        emitter = get_global_emitter()
        if emitter.active:
            payload = {
                "paths": [str(f.path) for f in files],
                "sources": [f.source for f in files],
                "truncated": [f.truncated for f in files],
            }
            emitter.emit_sync(
                HookEvent.INSTRUCTIONS_LOADED,
                tool_name="chimera.instruction_files",
                tool_input=payload,
            )
    except Exception:  # pragma: no cover - best-effort
        pass


def load_instruction_text(
    project_dir: Path | str | None = None,
) -> str:
    """Return the concatenated instruction text for all discovered files.

    Each file is wrapped in a ``<instructions source="..." path="...">``
    block so the model can attribute guidance back to its origin.
    Truncated files carry a trailing ``[truncated at NN KiB]`` marker.

    Args:
        project_dir: Project root anchor. Defaults to ``Path.cwd()``.

    Returns:
        Concatenated instruction text. Empty string when no files were
        discovered.
    """
    files = load_instruction_files(project_dir)
    if not files:
        return ""
    blocks: list[str] = []
    for f in files:
        body = f.text.rstrip()
        if f.truncated:
            body = body + f"\n\n[truncated at {_MAX_FILE_BYTES // 1024} KiB]"
        blocks.append(
            f'<instructions source="{f.source}" path="{f.path}">\n'
            f"{body}\n"
            f"</instructions>"
        )
    return "\n\n".join(blocks)
