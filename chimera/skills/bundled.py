"""Registry for skills bundled directly in code (not loaded from disk)."""
from __future__ import annotations

from chimera.commands.types import PromptCommand
from chimera.skills.definition import SkillDefinition

_bundled: list[SkillDefinition] = []


def register_bundled_skill(definition: SkillDefinition) -> None:
    """Add a skill definition to the bundled registry."""
    _bundled.append(definition)


def get_bundled_skills() -> list[PromptCommand]:
    """Return all bundled skills as :class:`PromptCommand` instances."""
    return [defn.to_command() for defn in _bundled]


def clear_bundled_skills() -> None:
    """Remove all bundled skills (useful for testing)."""
    _bundled.clear()
