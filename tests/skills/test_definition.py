"""Tests for chimera.skills.definition — Phase 7."""
from __future__ import annotations

from chimera.commands.types import CommandType
from chimera.skills.definition import SkillDefinition


class TestSkillDefinition:
    """SkillDefinition expands arguments and converts to PromptCommand."""

    def test_expand_arguments(self):
        defn = SkillDefinition(
            name="greet",
            description="Greet user",
            prompt_content="Hello $ARGUMENTS, welcome!",
        )
        assert defn._expand_prompt({"name": "Alice"}) == 'Hello {"name": "Alice"}, welcome!'
        assert defn._expand_prompt() == "Hello , welcome!"

    def test_to_command(self):
        defn = SkillDefinition(
            name="analyze",
            description="Analyze code",
            prompt_content="Please analyze $ARGUMENTS",
            allowed_tools=["bash", "read"],
            model="sonnet",
        )
        cmd = defn.to_command()
        assert cmd.type == CommandType.PROMPT
        assert cmd.name == "analyze"
        assert cmd.source == "skill"
        assert cmd.allowed_tools == ["bash", "read"]
        assert cmd.model == "sonnet"
        assert cmd.get_prompt({"file": "main.py"}) == 'Please analyze {"file": "main.py"}'
        assert cmd.content_length == len("Please analyze $ARGUMENTS")
