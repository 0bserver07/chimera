"""Skill definition and prompt expansion."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from chimera.commands.types import PromptCommand


@dataclass
class SkillDefinition:
    """Parsed representation of a skill loaded from disk or registered programmatically."""

    name: str
    description: str
    prompt_content: str
    allowed_tools: list[str] | None = None
    model: str | None = None
    context: str = "inline"
    arg_names: list[str] = field(default_factory=list)
    disable_model_invocation: bool = False
    source_path: Path | None = None

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def to_command(self) -> PromptCommand:
        """Convert this skill definition into a :class:`PromptCommand`."""
        defn = self  # capture for closure

        def _get_prompt(args: dict[str, str] | None = None) -> str:
            return defn._expand_prompt(args)

        return PromptCommand(
            name=defn.name,
            description=defn.description,
            allowed_tools=defn.allowed_tools,
            model=defn.model,
            source="skill",
            context=defn.context,
            content_length=len(defn.prompt_content),
            get_prompt=_get_prompt,
            disable_model_invocation=defn.disable_model_invocation,
            loaded_from=str(defn.source_path) if defn.source_path else "programmatic",
        )

    # ------------------------------------------------------------------
    # Prompt expansion
    # ------------------------------------------------------------------

    def _expand_prompt(self, args: dict[str, str] | None = None) -> str:
        """Replace ``$ARGUMENTS`` in the prompt content with *args* as JSON."""
        if args is None:
            return self.prompt_content.replace("$ARGUMENTS", "")
        return self.prompt_content.replace("$ARGUMENTS", json.dumps(args))
