from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from chimera.config.paths import store_path


@dataclass
class PromptTemplate:
    """A reusable prompt template with variable expansion."""

    name: str
    description: str = ""
    content: str = ""
    variables: dict[str, str] = field(default_factory=dict)  # var_name -> default_value
    source_path: Path | None = None

    def render(self, **kwargs: Any) -> str:
        """Render template, replacing {{variable}} with provided values or defaults."""
        result = self.content
        # Merge defaults with provided values
        all_vars = {**self.variables, **kwargs}
        for key, value in all_vars.items():
            result = result.replace(f"{{{{{key}}}}}", str(value))
        # Remove any unreplaced variables
        result = re.sub(r"\{\{[^}]+\}\}", "", result)
        return result.strip()

    @classmethod
    def from_file(cls, path: Path) -> PromptTemplate:
        """Load template from markdown file with YAML frontmatter."""
        content = path.read_text()
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                import yaml  # type: ignore[import-untyped]

                frontmatter = yaml.safe_load(parts[1]) or {}
                body = parts[2].strip()
                return cls(
                    name=frontmatter.get("name", path.stem),
                    description=frontmatter.get("description", ""),
                    content=body,
                    variables=frontmatter.get("variables", {}),
                    source_path=path,
                )
        return cls(name=path.stem, content=content, source_path=path)


class PromptTemplateLoader:
    """Load prompt templates from directories."""

    def __init__(self, search_paths: list[Path]):
        self._paths = search_paths

    def load_all(self) -> dict[str, PromptTemplate]:
        templates: dict[str, PromptTemplate] = {}
        for base in self._paths:
            prompts_dir = store_path("project-prompts", base)
            if not prompts_dir.exists():
                continue
            for f in prompts_dir.glob("*.md"):
                try:
                    t = PromptTemplate.from_file(f)
                    templates[t.name] = t
                except Exception:
                    pass
        return templates

    def get(self, name: str) -> PromptTemplate | None:
        all_templates = self.load_all()
        return all_templates.get(name)
