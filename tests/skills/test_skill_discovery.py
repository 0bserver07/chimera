"""Tests for chimera.skills.discovery."""
from pathlib import Path
from chimera.skills.discovery import (
    Skill, discover_skills, _parse_skill_file,
    default_search_paths, format_skills_for_prompt,
)


def _write_skill(path: Path, name: str, description: str, content: str = "Body") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: \"{description}\"\n---\n{content}")
    return path


def test_parse_skill_file(tmp_path):
    f = _write_skill(tmp_path / "my-skill" / "SKILL.md", "my-skill", "Does things")
    skill = _parse_skill_file(f)
    assert skill is not None
    assert skill.name == "my-skill"
    assert skill.description == "Does things"
    assert skill.content == "Body"


def test_parse_skill_no_frontmatter(tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text("No frontmatter here")
    assert _parse_skill_file(f) is None


def test_parse_skill_missing_name(tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text("---\ndescription: \"test\"\n---\nBody")
    assert _parse_skill_file(f) is None


def test_parse_skill_invalid_name(tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text("---\nname: UPPERCASE\ndescription: \"test\"\n---\nBody")
    assert _parse_skill_file(f) is None


def test_parse_skill_name_too_long(tmp_path):
    f = tmp_path / "SKILL.md"
    long_name = "a" * 65
    f.write_text(f"---\nname: {long_name}\ndescription: \"test\"\n---\nBody")
    assert _parse_skill_file(f) is None


def test_discover_skills(tmp_path):
    _write_skill(tmp_path / "alpha" / "SKILL.md", "alpha", "First skill", "Alpha content")
    _write_skill(tmp_path / "beta" / "SKILL.md", "beta", "Second skill", "Beta content")
    skills = discover_skills([str(tmp_path)])
    assert len(skills) == 2
    names = {s.name for s in skills}
    assert names == {"alpha", "beta"}


def test_discover_skills_dedup(tmp_path):
    dir1 = tmp_path / "dir1"
    dir2 = tmp_path / "dir2"
    _write_skill(dir1 / "same" / "SKILL.md", "same", "Version 1", "V1")
    _write_skill(dir2 / "same" / "SKILL.md", "same", "Version 2", "V2")
    skills = discover_skills([str(dir1), str(dir2)])
    assert len(skills) == 1
    assert skills[0].description == "Version 2"  # Last wins


def test_discover_skills_missing_dir():
    skills = discover_skills(["/nonexistent/path/xyz"])
    assert skills == []


def test_discover_skills_nested(tmp_path):
    _write_skill(tmp_path / "group" / "sub-skill" / "SKILL.md", "sub-skill", "Nested")
    skills = discover_skills([str(tmp_path)])
    assert len(skills) == 1
    assert skills[0].name == "sub-skill"


def test_default_search_paths():
    paths = default_search_paths("/my/project")
    assert len(paths) == 2
    assert paths[0] == Path("/my/project/.chimera/skills")


def test_format_skills_for_prompt():
    skills = [
        Skill(name="test", description="A test skill", content="", file_path="", base_dir=""),
    ]
    output = format_skills_for_prompt(skills)
    assert "## Available Skills" in output
    assert "**test**" in output
    assert "A test skill" in output


def test_format_skills_empty():
    assert format_skills_for_prompt([]) == ""
