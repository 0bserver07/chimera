"""Load skill definitions from .chimera/skills/*.md files."""
from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]

from chimera.skills.definition import SkillDefinition


class SkillLoader:
    """Discovers and parses skill markdown files from search paths."""

    def __init__(self, search_paths: list[Path]) -> None:
        self._search_paths = search_paths

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def load_all(self) -> list[SkillDefinition]:
        """Find ``*.md`` files in all search paths and parse them."""
        skills: dict[str, SkillDefinition] = {}
        for path in self._search_paths:
            if not path.exists():
                continue
            for md_file in sorted(path.glob("*.md")):
                defn = self._parse_skill_file(md_file)
                if defn is not None:
                    skills[defn.name] = defn
        return list(skills.values())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_skill_file(path: Path) -> SkillDefinition | None:
        """Parse a skill markdown file with YAML frontmatter.

        Expected format::

            ---
            name: my-skill
            description: What this skill does
            allowed_tools:
              - bash
              - read
            ---
            Prompt body goes here.  $ARGUMENTS will be expanded.
        """
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            return None

        if not text.startswith("---"):
            return None

        parts = text.split("---", 2)
        if len(parts) < 3:
            return None

        frontmatter_str = parts[1].strip()
        body = parts[2].strip()

        try:
            meta = yaml.safe_load(frontmatter_str)
        except yaml.YAMLError:
            return None

        if not isinstance(meta, dict):
            return None

        name = meta.get("name")
        description = meta.get("description")
        if not name or not description:
            return None

        return SkillDefinition(
            name=name,
            description=description,
            prompt_content=body,
            allowed_tools=meta.get("allowed_tools"),
            model=meta.get("model"),
            context=meta.get("context", "inline"),
            arg_names=meta.get("arg_names", []),
            disable_model_invocation=meta.get("disable_model_invocation", False),
            source_path=path,
        )
