"""Discover skills from SKILL.md files with YAML frontmatter."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    """A discovered skill from a SKILL.md file."""
    name: str
    description: str
    content: str
    file_path: str
    base_dir: str


_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def discover_skills(search_paths: list[str | Path]) -> list[Skill]:
    """Walk directories for SKILL.md files and parse them.

    Search paths are checked in order. Later paths can override
    earlier ones (by skill name).

    Args:
        search_paths: Directories to search for SKILL.md files.

    Returns:
        Deduplicated list of skills (last wins by name).
    """
    skills_by_name: dict[str, Skill] = {}
    for path in search_paths:
        p = Path(path)
        if not p.exists():
            continue
        for skill_file in sorted(p.rglob("SKILL.md")):
            skill = _parse_skill_file(skill_file)
            if skill is not None:
                skills_by_name[skill.name] = skill
    return list(skills_by_name.values())


def _parse_skill_file(path: Path) -> Skill | None:
    """Parse a SKILL.md file with YAML frontmatter.

    Expected format:
        ---
        name: my-skill
        description: "What this skill does"
        ---
        Skill content (markdown)

    Returns None if parsing fails or validation fails.
    """
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return None

    if not text.startswith("---"):
        return None

    # Split frontmatter
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter = parts[1].strip()
    content = parts[2].strip()

    # Parse YAML frontmatter (simple key: value parsing, no pyyaml dependency)
    meta: dict[str, str] = {}
    for line in frontmatter.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            meta[key] = value

    name = meta.get("name", "")
    description = meta.get("description", "")

    if not name or not description:
        return None

    # Validate name format
    if not _NAME_PATTERN.match(name):
        return None

    # Validate description length
    if len(description) > 1024:
        return None

    return Skill(
        name=name,
        description=description,
        content=content,
        file_path=str(path),
        base_dir=str(path.parent),
    )


def default_search_paths(workdir: str = ".") -> list[Path]:
    """Return default skill search paths in priority order.

    1. {workdir}/.chimera/skills/ (project-local)
    2. ~/.chimera/skills/ (user global)
    """
    return [
        Path(workdir) / ".chimera" / "skills",
        Path.home() / ".chimera" / "skills",
    ]


def format_skills_for_prompt(skills: list[Skill]) -> str:
    """Format discovered skills for system prompt injection.

    Returns empty string if no skills found.
    """
    if not skills:
        return ""
    lines = ["## Available Skills"]
    for s in skills:
        lines.append(f"- **{s.name}**: {s.description}")
    return "\n".join(lines)
