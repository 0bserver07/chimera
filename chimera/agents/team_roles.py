"""Discover team-role templates from agent ``.md`` files.

A team role is an agent definition whose YAML frontmatter sets
``team_role: <name>``. The same agent profile (executor, planner,
researcher, reviewer) can therefore be re-used as a template when
spinning up a team via :class:`chimera.composition.Team` or similar
coordinator workflows.

This module walks the same priority chain the rest of the agent
loader uses — project > user > built-in — so a project-local
override at ``.chimera/agents/<name>.md`` wins over the bundled
preset of the same role.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.agents.config import _parse_frontmatter
from chimera.agents.loader import builtin_subagents_dir

__all__ = ["discover_team_roles"]


def _collect_from_dir(directory: Path, accumulator: dict[str, dict[str, Any]]) -> None:
    """Walk a directory for ``.md`` files and add new team roles.

    First role-name wins, which means callers must seed ``accumulator``
    in priority order (highest-priority directory first).
    """
    if not directory.is_dir():
        return
    for md_file in sorted(directory.glob("*.md")):
        try:
            text = md_file.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if not text.startswith("---"):
            continue
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        meta = _parse_frontmatter(parts[1])

        role_raw = meta.get("team_role")
        if not role_raw or isinstance(role_raw, list):
            continue
        role = str(role_raw).strip()
        if not role or role in accumulator:
            continue

        tools_raw = meta.get("tools")
        tools: list[str] | None
        if isinstance(tools_raw, list):
            tools = [str(t) for t in tools_raw]
        else:
            tools = None

        model_raw = meta.get("model")
        model = (
            str(model_raw)
            if model_raw and not isinstance(model_raw, list)
            else None
        )

        description_raw = meta.get("description", "")
        description = (
            str(description_raw)
            if not isinstance(description_raw, list)
            else ""
        )

        accumulator[role] = {
            "role": role,
            "source_path": str(md_file),
            "description": description,
            "tools": tools,
            "model": model,
        }


def discover_team_roles(workdir: Path | None = None) -> list[dict[str, Any]]:
    """Discover team-role templates across project, user, and built-in dirs.

    Priority (first-found wins for a given ``team_role`` value):

    1. ``<workdir>/.chimera/agents/`` — project overrides
    2. ``~/.chimera/agents/`` — user overrides
    3. ``chimera/agents/presets/subagents/`` — packaged defaults

    Args:
        workdir: Optional project root. Defaults to the current working
            directory.

    Returns:
        A list of dicts ``{role, source_path, description, tools, model}``
        sorted alphabetically by ``role``. ``tools`` is ``list[str] | None``
        (``None`` when the file omits the field). ``model`` is
        ``str | None`` (``None`` when the file omits the field).
    """
    root = workdir if workdir is not None else Path.cwd()

    accumulator: dict[str, dict[str, Any]] = {}
    _collect_from_dir(root / ".chimera" / "agents", accumulator)
    _collect_from_dir(Path.home() / ".chimera" / "agents", accumulator)
    _collect_from_dir(builtin_subagents_dir(), accumulator)

    return sorted(accumulator.values(), key=lambda entry: entry["role"])
