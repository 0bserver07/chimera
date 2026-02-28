"""Project configuration loader -- discovers AGENTS.md, CLAUDE.md, and rules files."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from chimera.config.skills import Skill, SkillRegistry


class ConfigSource(ABC):
    """Abstract source for project rules."""

    @abstractmethod
    def load(self) -> list[str]:
        """Load rules as a list of text blocks."""


class FileConfigSource(ConfigSource):
    """Load rules from a file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> list[str]:
        if self._path.exists():
            return [self._path.read_text()]
        return []


# Default rules files, in priority order
_RULES_FILES = ("AGENTS.md", "CLAUDE.md", ".chimera/rules.md")


class ProjectConfig:
    """Discovers and aggregates project-level configuration.

    Searches for rules files (AGENTS.md, CLAUDE.md), skills directories,
    and other project-level settings.

    Example:
        ```python
        config = ProjectConfig.from_directory("./myapp")
        print(config.rules_text)  # Concatenated rules
        skill = config.get_skill("debugging")
        ```
    """

    def __init__(
        self,
        rules: list[str] | None = None,
        rules_files: list[str] | None = None,
        skills_dirs: list[str] | None = None,
        root: Path | None = None,
    ) -> None:
        self._root = root or Path.cwd()
        self._rules = rules or []
        self._rules_files = rules_files or list(_RULES_FILES)
        self._sources: list[ConfigSource] = []
        self._skills = SkillRegistry(
            [self._root / d for d in (skills_dirs or ["skills"])]
        )

        # Build sources from rules_files
        for rf in self._rules_files:
            self._sources.append(FileConfigSource(self._root / rf))

    @classmethod
    def from_directory(cls, path: str) -> ProjectConfig:
        """Auto-discover configuration from a project directory.

        Args:
            path: Project root directory.

        Returns:
            ProjectConfig with discovered rules and skills.
        """
        root = Path(path).resolve()
        # Discover skills directories
        skills_dirs = []
        for candidate in ("skills", ".chimera/skills", ".claude/skills"):
            if (root / candidate).is_dir():
                skills_dirs.append(candidate)
        return cls(root=root, skills_dirs=skills_dirs or ["skills"])

    @property
    def rules_text(self) -> str:
        """Concatenated text of all rules sources."""
        blocks = list(self._rules)
        for source in self._sources:
            blocks.extend(source.load())
        return "\n\n---\n\n".join(b for b in blocks if b.strip())

    def get_skill(self, name: str) -> Skill | None:
        """Look up a skill by name.

        Args:
            name: Skill name.

        Returns:
            Skill if found, None otherwise.
        """
        return self._skills.get(name)

    @property
    def skill_names(self) -> list[str]:
        """List all discovered skill names."""
        return self._skills.names
