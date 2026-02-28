"""Skill discovery and loading from SKILL.md files."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Skill:
    """An on-demand instruction set loaded from a SKILL.md file.

    Args:
        name: Skill name (from directory name or frontmatter).
        content: Full markdown content of the skill.
        description: Short description (from frontmatter).
        args: Expected arguments (from frontmatter).
    """

    name: str
    content: str
    description: str = ""
    args: list[str] = field(default_factory=list)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML-like frontmatter from markdown.

    Returns:
        Tuple of (metadata dict, remaining content).
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("---", 3)
    if end == -1:
        return {}, text
    front = text[3:end].strip()
    body = text[end + 3:].strip()
    meta: dict[str, str] = {}
    for line in front.split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, body


class SkillRegistry:
    """Discovers skills from directories containing SKILL.md files.

    Skills are loaded lazily -- only read from disk when accessed.
    """

    def __init__(self, dirs: list[Path]) -> None:
        self._dirs = dirs
        self._cache: dict[str, Skill] = {}
        self._discovered: dict[str, Path] | None = None

    def _discover(self) -> dict[str, Path]:
        """Walk skill directories and find SKILL.md files."""
        if self._discovered is not None:
            return self._discovered
        self._discovered = {}
        for skill_dir in self._dirs:
            if not skill_dir.is_dir():
                continue
            for skill_file in skill_dir.rglob("SKILL.md"):
                name = skill_file.parent.name
                self._discovered[name] = skill_file
        return self._discovered

    def get(self, name: str) -> Skill | None:
        """Load a skill by name.

        Args:
            name: Skill name (directory name).

        Returns:
            Skill if found, None otherwise.
        """
        if name in self._cache:
            return self._cache[name]
        paths = self._discover()
        path = paths.get(name)
        if path is None:
            return None
        text = path.read_text()
        meta, body = _parse_frontmatter(text)
        skill = Skill(
            name=meta.get("name", name),
            content=body,
            description=meta.get("description", ""),
            args=[a.strip() for a in meta.get("args", "").split(",") if a.strip()],
        )
        self._cache[name] = skill
        return skill

    @property
    def names(self) -> list[str]:
        """List all discovered skill names."""
        return sorted(self._discover().keys())
