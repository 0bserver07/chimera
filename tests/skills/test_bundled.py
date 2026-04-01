"""Tests for chimera.skills.bundled — Phase 7."""
from __future__ import annotations

from chimera.commands.types import CommandType
from chimera.skills.bundled import clear_bundled_skills, get_bundled_skills, register_bundled_skill
from chimera.skills.definition import SkillDefinition


class TestBundledSkills:
    """Bundled skill module-level registry."""

    def setup_method(self):
        clear_bundled_skills()

    def teardown_method(self):
        clear_bundled_skills()

    def test_register_and_get(self):
        defn = SkillDefinition(
            name="bundled-one",
            description="A bundled skill",
            prompt_content="Do the thing",
        )
        register_bundled_skill(defn)
        commands = get_bundled_skills()
        assert len(commands) == 1
        assert commands[0].name == "bundled-one"
        assert commands[0].type == CommandType.PROMPT

    def test_clear(self):
        register_bundled_skill(SkillDefinition(
            name="temp",
            description="Temporary",
            prompt_content="...",
        ))
        assert len(get_bundled_skills()) == 1
        clear_bundled_skills()
        assert len(get_bundled_skills()) == 0
