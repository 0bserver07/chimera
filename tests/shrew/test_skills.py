"""Tests for chimera.shrew.skills discovery + frontmatter parsing."""
from __future__ import annotations

from pathlib import Path

import pytest

from chimera.shrew.skills import (
    CATEGORIES,
    SKILLS_ROOT,
    ShrewSkill,
    discover_shrew_skills,
    format_shrew_skills_for_prompt,
)
from chimera.shrew.skills import _parse_frontmatter, _parse_skill, _parse_triggers


# Skills the bundled set is expected to ship. If you add or rename a skill
# under chimera/shrew/skills/, update this set.
EXPECTED_BUNDLED: dict[str, str] = {
    # knowledge
    "context-window-discipline": "knowledge",
    "scaffold-model-fit": "knowledge",
    "escalation-signals": "knowledge",
    "python-idioms": "knowledge",
    "loop-detection-signals": "knowledge",
    "tool-budget-vs-prose-budget": "knowledge",
    "git-aware-context": "knowledge",
    # protocols
    "edit-before-write": "protocols",
    "test-first-python": "protocols",
    "one-focused-question": "protocols",
    "error-recovery": "protocols",
    "bisect-on-failure": "protocols",
    "dry-run-before-commit": "protocols",
    "read-tests-before-fixing": "protocols",
    "incremental-edits": "protocols",
    # tools
    "core-tools": "tools",
    "grep-vs-ls": "tools",
    "multi-file-edits": "tools",
    "bash-pipelines-with-care": "tools",
    "find-vs-grep-vs-rg": "tools",
    "python-subprocess-vs-bash": "tools",
}


def test_skills_root_exists() -> None:
    assert SKILLS_ROOT.is_dir()
    for cat in CATEGORIES:
        assert (SKILLS_ROOT / cat).is_dir(), f"missing category dir: {cat}"


def test_categories_canonical_order() -> None:
    assert CATEGORIES == ("knowledge", "protocols", "tools")


def test_discover_returns_expected_set() -> None:
    skills = discover_shrew_skills()
    by_name = {s.name: s for s in skills}
    assert set(by_name.keys()) == set(EXPECTED_BUNDLED.keys()), (
        f"bundled skill set drift: extra={set(by_name) - set(EXPECTED_BUNDLED)} "
        f"missing={set(EXPECTED_BUNDLED) - set(by_name)}"
    )
    for name, expected_cat in EXPECTED_BUNDLED.items():
        assert by_name[name].category == expected_cat, (
            f"{name} expected category {expected_cat}, got {by_name[name].category}"
        )


def test_skill_count_within_spec_bounds() -> None:
    skills = discover_shrew_skills()
    # Curated set: at least 21 skills after wave-9 expansion (knowledge,
    # protocols, tools combined). Upper bound stays loose so future waves
    # can extend without churning the test.
    assert len(skills) >= 21, f"got {len(skills)} skills, expected at least 21"
    assert len(skills) <= 40, f"got {len(skills)} skills, suspiciously many"


def test_each_skill_has_well_formed_frontmatter() -> None:
    skills = discover_shrew_skills()
    for s in skills:
        assert isinstance(s, ShrewSkill)
        # Name format
        assert s.name and s.name.islower()
        assert all(c.isalnum() or c == "-" for c in s.name)
        # Description
        assert s.description
        assert 5 <= len(s.description) <= 256
        # Body has substance.
        body_lines = [line for line in s.body.splitlines() if line.strip()]
        assert len(body_lines) >= 5, f"{s.name} body too short ({len(body_lines)} lines)"
        # Path references the bundled root.
        assert Path(s.path).is_file()
        assert str(SKILLS_ROOT) in s.path


def test_each_skill_declares_triggers() -> None:
    skills = discover_shrew_skills()
    for s in skills:
        # Every shrew skill should declare at least one trigger phrase.
        assert s.triggers, f"{s.name} has no triggers declared"
        for trig in s.triggers:
            assert trig.strip() == trig
            assert len(trig) >= 2


def test_discover_is_sorted_stably() -> None:
    skills = discover_shrew_skills()
    cat_index = {c: i for i, c in enumerate(CATEGORIES)}
    keys = [(cat_index[s.category], s.name) for s in skills]
    assert keys == sorted(keys)


def test_extra_search_paths_override_bundled(tmp_path: Path) -> None:
    cat_dir = tmp_path / "protocols"
    cat_dir.mkdir(parents=True)
    override = cat_dir / "edit-before-write.md"
    override.write_text(
        '---\nname: edit-before-write\ndescription: "User override"\n'
        'triggers: ["override"]\n---\n'
        "Body line one.\nBody line two.\nBody line three.\n"
        "Body line four.\nBody line five.\n",
        encoding="utf-8",
    )
    skills = discover_shrew_skills(extra_search_paths=[tmp_path])
    by_name = {s.name: s for s in skills}
    assert by_name["edit-before-write"].description == "User override"
    assert by_name["edit-before-write"].triggers == ("override",)


def test_extra_search_paths_can_add_new_skill(tmp_path: Path) -> None:
    cat_dir = tmp_path / "knowledge"
    cat_dir.mkdir(parents=True)
    new_skill = cat_dir / "extra.md"
    new_skill.write_text(
        '---\nname: extra-skill\ndescription: "Local addition"\n'
        'triggers: ["extra"]\n---\n'
        "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n",
        encoding="utf-8",
    )
    skills = discover_shrew_skills(extra_search_paths=[tmp_path])
    names = {s.name for s in skills}
    assert "extra-skill" in names


def test_extra_search_paths_missing_dir_is_silent(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    skills = discover_shrew_skills(extra_search_paths=[missing])
    assert {s.name for s in skills} == set(EXPECTED_BUNDLED)


def test_format_for_prompt_groups_by_category() -> None:
    skills = discover_shrew_skills()
    rendered = format_shrew_skills_for_prompt(skills)
    assert rendered.startswith("## Shrew skills")
    for cat in CATEGORIES:
        assert f"### {cat}" in rendered
    # Every skill name shows up in the rendered block.
    for s in skills:
        assert f"**{s.name}**" in rendered


def test_format_for_prompt_empty_input_yields_empty_string() -> None:
    assert format_shrew_skills_for_prompt([]) == ""


# ---------------------------------------------------------------------------
# Internal-helper coverage (kept narrow to lock parser semantics).
# ---------------------------------------------------------------------------


def test_parse_frontmatter_strips_quotes() -> None:
    meta = _parse_frontmatter('name: foo\ndescription: "bar baz"\n')
    assert meta == {"name": "foo", "description": "bar baz"}


def test_parse_frontmatter_ignores_blank_and_comment_lines() -> None:
    meta = _parse_frontmatter("\n# comment\nname: foo\n   \n")
    assert meta == {"name": "foo"}


def test_parse_triggers_handles_list_form() -> None:
    assert _parse_triggers('["a", "b", "c"]') == ("a", "b", "c")


def test_parse_triggers_handles_csv_form() -> None:
    assert _parse_triggers("a, b, c") == ("a", "b", "c")


def test_parse_triggers_handles_empty() -> None:
    assert _parse_triggers("") == ()
    assert _parse_triggers("[]") == ()


def test_parse_skill_rejects_missing_frontmatter(tmp_path: Path) -> None:
    f = tmp_path / "x.md"
    f.write_text("no frontmatter here\n")
    assert _parse_skill(f, "knowledge") is None


def test_parse_skill_rejects_bad_name(tmp_path: Path) -> None:
    f = tmp_path / "x.md"
    f.write_text(
        '---\nname: BadName\ndescription: "x"\n---\nbody\n', encoding="utf-8"
    )
    assert _parse_skill(f, "knowledge") is None


def test_parse_skill_requires_description(tmp_path: Path) -> None:
    f = tmp_path / "x.md"
    f.write_text("---\nname: ok\n---\nbody\n", encoding="utf-8")
    assert _parse_skill(f, "knowledge") is None


@pytest.mark.parametrize("category", list(CATEGORIES))
def test_each_category_has_at_least_two_skills(category: str) -> None:
    skills = [s for s in discover_shrew_skills() if s.category == category]
    # Spec asks for 3-4 / 3-4 / 2-4 in (knowledge, protocols, tools).
    assert len(skills) >= 2, f"{category} has only {len(skills)} skills"
