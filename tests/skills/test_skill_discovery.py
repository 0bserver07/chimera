"""Tests for chimera.skills.discovery."""
from pathlib import Path

import pytest

from chimera.skills.discovery import (
    Skill, discover_skills, _parse_skill_file,
    default_search_paths, format_skills_for_prompt,
    default_foreign_skill_dirs, discover_foreign_skills,
    discover_all_skills, resolve_foreign_config,
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
    # Layout (W13-G12): bundled algorithm skills + project + user.
    # Bundled comes first so project / user paths override by name.
    paths = default_search_paths("/my/project")
    assert len(paths) == 3
    assert paths[0].name == "algorithms"   # bundled in-tree set
    assert Path("/my/project/.chimera/skills") in paths
    assert (Path.home() / ".chimera" / "skills") in paths


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


# ---------------------------------------------------------------------------
# Cross-harness skill interop (Tier-2 T5)
# ---------------------------------------------------------------------------


def _isolate_foreign_env(monkeypatch, tmp_path):
    """Point discovery at an empty config home and clear the toggle env var.

    Ensures a test never picks up the developer's real
    ``~/.chimera/config.toml`` or an ambient ``CHIMERA_SKILLS_FOREIGN``.
    """
    cfg = tmp_path / "cfg-home"
    cfg.mkdir(exist_ok=True)
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(cfg))
    monkeypatch.delenv("CHIMERA_SKILLS_FOREIGN", raising=False)


def test_default_foreign_skill_dirs():
    dirs = default_foreign_skill_dirs()
    assert dirs == ["~/.claude/skills", "~/.codex/skills", "~/.agents/skills"]
    # Returns a fresh list — mutating it must not affect later calls.
    dirs.append("mutated")
    assert "mutated" not in default_foreign_skill_dirs()


def test_discover_foreign_skills_tags_source(tmp_path):
    d = tmp_path / "other-harness" / "skills"
    _write_skill(d / "foreign-alpha" / "SKILL.md", "foreign-alpha", "From another harness")
    skills = discover_foreign_skills([str(d)])
    assert len(skills) == 1
    assert skills[0].name == "foreign-alpha"
    assert skills[0].source == str(d)


def test_discover_foreign_skills_expands_user(tmp_path, monkeypatch):
    # A ``~`` in an allowlist entry is expanded at scan time via $HOME, but
    # the source label keeps the ``~`` form for a readable provenance tag.
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_skill(tmp_path / ".codex" / "skills" / "fx" / "SKILL.md", "fx", "a codex skill")
    skills = discover_foreign_skills(["~/.codex/skills"])
    assert [s.name for s in skills] == ["fx"]
    assert skills[0].source == "~/.codex/skills"


def test_discover_foreign_skills_allowlist_precedence(tmp_path):
    d1 = tmp_path / "first"
    d2 = tmp_path / "second"
    _write_skill(d1 / "dup" / "SKILL.md", "dup", "first wins", "A")
    _write_skill(d2 / "dup" / "SKILL.md", "dup", "second loses", "B")
    skills = discover_foreign_skills([str(d1), str(d2)])
    assert len(skills) == 1
    assert skills[0].description == "first wins"  # earlier allowlist entry wins
    assert skills[0].source == str(d1)


def test_discover_foreign_skills_missing_dir():
    assert discover_foreign_skills(["/nonexistent/foreign/xyz"]) == []


def test_discover_all_skills_default_off(tmp_path, monkeypatch):
    # No config + no env → foreign scan disabled → foreign skills absent even
    # when their directory is handed in explicitly. Pins the safe default.
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    _isolate_foreign_env(monkeypatch, tmp_path)
    foreign = tmp_path / "foreign"
    _write_skill(foreign / "foreign-beta" / "SKILL.md", "foreign-beta", "should not appear")
    skills = discover_all_skills(str(tmp_path / "proj"), foreign_dirs=[str(foreign)])
    assert "foreign-beta" not in {s.name for s in skills}


def test_discover_all_skills_include_foreign_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    _isolate_foreign_env(monkeypatch, tmp_path)
    foreign = tmp_path / "foreign"
    _write_skill(foreign / "foreign-gamma" / "SKILL.md", "foreign-gamma", "third-party skill")
    skills = discover_all_skills(
        str(tmp_path / "proj"), include_foreign=True, foreign_dirs=[str(foreign)]
    )
    by_name = {s.name: s for s in skills}
    assert "foreign-gamma" in by_name
    assert by_name["foreign-gamma"].source == str(foreign)


def test_discover_all_skills_native_wins_over_foreign(tmp_path, monkeypatch):
    # A project (native) skill outranks a same-named foreign skill.
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    _isolate_foreign_env(monkeypatch, tmp_path)
    proj = tmp_path / "proj"
    _write_skill(proj / ".chimera" / "skills" / "shared" / "SKILL.md", "shared", "native version")
    foreign = tmp_path / "foreign"
    _write_skill(foreign / "shared" / "SKILL.md", "shared", "foreign version")
    skills = discover_all_skills(str(proj), include_foreign=True, foreign_dirs=[str(foreign)])
    shared = [s for s in skills if s.name == "shared"]
    assert len(shared) == 1
    assert shared[0].source == "chimera"
    assert shared[0].description == "native version"


def test_discover_all_skills_reads_config(tmp_path, monkeypatch):
    # End-to-end: the config chain toggles the scan (no include_foreign arg).
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.delenv("CHIMERA_SKILLS_FOREIGN", raising=False)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    foreign = tmp_path / "foreign"
    _write_skill(foreign / "cfg-skill" / "SKILL.md", "cfg-skill", "enabled via config")
    (cfg / "config.toml").write_text(
        f'[skills]\nscan-foreign = true\nforeign-dirs = ["{foreign}"]\n'
    )
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(cfg))
    skills = discover_all_skills(str(tmp_path / "proj"))
    assert "cfg-skill" in {s.name for s in skills}


def test_resolve_foreign_config_default(tmp_path, monkeypatch):
    _isolate_foreign_env(monkeypatch, tmp_path)
    enabled, dirs = resolve_foreign_config()
    assert enabled is False
    assert dirs == default_foreign_skill_dirs()


def test_resolve_foreign_config_from_toml(tmp_path, monkeypatch):
    monkeypatch.delenv("CHIMERA_SKILLS_FOREIGN", raising=False)
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        '[skills]\nscan-foreign = true\nforeign-dirs = ["~/.codex/skills", "/opt/skills"]\n'
    )
    enabled, dirs = resolve_foreign_config()
    assert enabled is True
    assert dirs == ["~/.codex/skills", "/opt/skills"]


def test_resolve_foreign_config_env_enables(tmp_path, monkeypatch):
    # The env var enables the scan even with no config file present.
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("CHIMERA_SKILLS_FOREIGN", "1")
    enabled, dirs = resolve_foreign_config()
    assert enabled is True
    assert dirs == default_foreign_skill_dirs()


def test_resolve_foreign_config_env_overrides_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text("[skills]\nscan-foreign = true\n")
    monkeypatch.setenv("CHIMERA_SKILLS_FOREIGN", "off")
    enabled, _dirs = resolve_foreign_config()
    assert enabled is False  # env "off" beats config "on"


@pytest.mark.parametrize("value,expected", [("1", True), ("true", True),
                                            ("yes", True), ("on", True)])
def test_resolve_foreign_config_env_truthy_forms(tmp_path, monkeypatch, value, expected):
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("CHIMERA_SKILLS_FOREIGN", value)
    enabled, _ = resolve_foreign_config()
    assert enabled is expected


def test_format_skills_for_prompt_labels_foreign():
    skills = [
        Skill(name="native", description="A native skill", content="", file_path="", base_dir=""),
        Skill(
            name="foreign", description="A foreign skill", content="",
            file_path="", base_dir="", source="~/.codex/skills",
        ),
    ]
    out = format_skills_for_prompt(skills)
    assert "- **native**: A native skill" in out
    assert "_(source: ~/.codex/skills)_" in out
    assert "read-only, third-party" in out  # provenance note present
    # The native line stays unlabeled.
    assert "- **native**: A native skill  _(source" not in out


def test_format_skills_for_prompt_native_only_unchanged():
    # With no foreign skills, output is the plain form (no provenance note).
    skills = [Skill(name="only", description="just native", content="", file_path="", base_dir="")]
    out = format_skills_for_prompt(skills)
    assert out == "## Available Skills\n- **only**: just native"
