# tests/test_config.py

import pytest

from chimera.config.loader import FileConfigSource, ProjectConfig
from chimera.config.skills import SkillRegistry, _parse_frontmatter
from chimera.config.structured import StructuredOutput, ValidationError


# ---- Frontmatter parsing ----


class TestParseFrontmatter:
    def test_with_frontmatter(self):
        text = "---\nname: test\ndescription: A test skill\n---\n# Content"
        meta, body = _parse_frontmatter(text)
        assert meta["name"] == "test"
        assert meta["description"] == "A test skill"
        assert body == "# Content"

    def test_without_frontmatter(self):
        text = "# Just markdown"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == "# Just markdown"

    def test_unclosed_frontmatter(self):
        text = "---\nname: test\nno closing"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == text


# ---- SkillRegistry ----


class TestSkillRegistry:
    def test_discover_skills(self, tmp_path):
        skill_dir = tmp_path / "skills" / "debugging"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: debugging\ndescription: Debug things\n---\n# Steps\n1. Check logs"
        )
        registry = SkillRegistry([tmp_path / "skills"])
        assert "debugging" in registry.names

    def test_get_skill(self, tmp_path):
        skill_dir = tmp_path / "skills" / "review"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: code-review\ndescription: Review code\nargs: file, scope\n---\n# Review"
        )
        registry = SkillRegistry([tmp_path / "skills"])
        skill = registry.get("review")
        assert skill is not None
        assert skill.name == "code-review"
        assert skill.description == "Review code"
        assert skill.args == ["file", "scope"]
        assert "# Review" in skill.content

    def test_get_missing_skill(self, tmp_path):
        registry = SkillRegistry([tmp_path / "skills"])
        assert registry.get("nonexistent") is None

    def test_caching(self, tmp_path):
        skill_dir = tmp_path / "skills" / "cached"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Cached skill")
        registry = SkillRegistry([tmp_path / "skills"])
        s1 = registry.get("cached")
        s2 = registry.get("cached")
        assert s1 is s2

    def test_empty_dir(self, tmp_path):
        registry = SkillRegistry([tmp_path / "nonexistent"])
        assert registry.names == []


# ---- FileConfigSource ----


class TestFileConfigSource:
    def test_existing_file(self, tmp_path):
        f = tmp_path / "rules.md"
        f.write_text("Always use ruff.")
        source = FileConfigSource(f)
        assert source.load() == ["Always use ruff."]

    def test_missing_file(self, tmp_path):
        source = FileConfigSource(tmp_path / "missing.md")
        assert source.load() == []


# ---- ProjectConfig ----


class TestProjectConfig:
    def test_from_directory(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# Rules\nUse pytest.")
        config = ProjectConfig.from_directory(str(tmp_path))
        assert "Use pytest" in config.rules_text

    def test_explicit_rules(self, tmp_path):
        config = ProjectConfig(rules=["Always format with black."], root=tmp_path)
        assert "Always format with black." in config.rules_text

    def test_rules_files_priority(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Agent rules here.")
        (tmp_path / "CLAUDE.md").write_text("Claude rules here.")
        config = ProjectConfig.from_directory(str(tmp_path))
        text = config.rules_text
        assert "Agent rules" in text
        assert "Claude rules" in text

    def test_no_rules_files(self, tmp_path):
        config = ProjectConfig.from_directory(str(tmp_path))
        assert config.rules_text == ""

    def test_skill_discovery(self, tmp_path):
        skill_dir = tmp_path / "skills" / "tdd"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# TDD\nRed-green-refactor.")
        config = ProjectConfig.from_directory(str(tmp_path))
        assert "tdd" in config.skill_names
        skill = config.get_skill("tdd")
        assert skill is not None
        assert "Red-green-refactor" in skill.content

    def test_get_missing_skill(self, tmp_path):
        config = ProjectConfig.from_directory(str(tmp_path))
        assert config.get_skill("nonexistent") is None


# ---- StructuredOutput ----


class TestStructuredOutput:
    def test_valid_json(self):
        schema = StructuredOutput(
            name="review",
            schema={
                "type": "object",
                "properties": {"score": {"type": "integer"}, "summary": {"type": "string"}},
                "required": ["score", "summary"],
            },
        )
        data = schema.validate('{"score": 8, "summary": "Good code"}')
        assert data["score"] == 8
        assert data["summary"] == "Good code"

    def test_invalid_json(self):
        schema = StructuredOutput(name="test", schema={"type": "object"})
        with pytest.raises(ValidationError, match="Invalid JSON"):
            schema.validate("not json at all")

    def test_missing_required_field(self):
        schema = StructuredOutput(
            name="test",
            schema={"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}},
        )
        with pytest.raises(ValidationError, match="missing required"):
            schema.validate('{"other": "value"}')

    def test_wrong_type(self):
        schema = StructuredOutput(
            name="test",
            schema={"type": "object", "properties": {"count": {"type": "integer"}}},
        )
        with pytest.raises(ValidationError, match="expected integer"):
            schema.validate('{"count": "not a number"}')

    def test_extract_from_code_block(self):
        schema = StructuredOutput(
            name="test",
            schema={"type": "object", "properties": {"x": {"type": "integer"}}},
        )
        text = '```json\n{"x": 42}\n```'
        data = schema.validate(text)
        assert data["x"] == 42

    def test_format_error(self):
        schema = StructuredOutput(name="review", schema={"type": "object"})
        err = ValidationError("bad json")
        msg = schema.format_error(err)
        assert "review" in msg
        assert "bad json" in msg
