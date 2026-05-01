"""Tests for ``chimera.weasel.prompt_templates`` — markdown frontmatter.

Exercises the loader against synthetic prompt-template directories
materialized under ``tmp_path``. Mirrors the themes test layout.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.weasel.prompt_templates import (
    PromptTemplate,
    get_prompt_template,
    load_prompt_templates,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Return a fresh project root under tmp_path."""
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def user_root(tmp_path: Path) -> Path:
    """Return a fresh user prompt root under tmp_path."""
    root = tmp_path / "user-prompts"
    root.mkdir()
    return root


def _write_template(
    root: Path,
    filename: str,
    body: str,
) -> Path:
    """Write a markdown template file under ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    target = root / filename
    target.write_text(body, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Built-in template
# ---------------------------------------------------------------------------


def test_load_includes_default_builtin(
    project_root: Path,
    user_root: Path,
) -> None:
    """``default`` is always present after a load."""
    registry = load_prompt_templates(project_root, user_root=user_root)
    assert "default" in registry
    assert registry["default"].system_prompt


def test_default_system_prompt_mentions_weasel() -> None:
    """The built-in default mentions weasel + tools so it's identifiable."""
    template = get_prompt_template("default")
    assert "Weasel" in template.system_prompt


# ---------------------------------------------------------------------------
# get_prompt_template lookup
# ---------------------------------------------------------------------------


def test_get_unknown_falls_back_to_default() -> None:
    """Unknown names fall back to the default template."""
    template = get_prompt_template("not-a-real-template")
    assert template.name == "default"


def test_get_none_falls_back_to_default() -> None:
    """``None`` resolves to default."""
    assert get_prompt_template(None).name == "default"


def test_get_strips_whitespace() -> None:
    """Whitespace-padded names resolve correctly."""
    custom = {
        "default": PromptTemplate(name="default", system_prompt="d"),
        "review": PromptTemplate(name="review", system_prompt="r"),
    }
    assert get_prompt_template("  review  ", registry=custom).name == "review"


def test_get_with_supplied_registry() -> None:
    """Explicit registries override the module-level cache."""
    custom = {
        "default": PromptTemplate(name="default", system_prompt="custom"),
    }
    template = get_prompt_template("default", registry=custom)
    assert template.system_prompt == "custom"


# ---------------------------------------------------------------------------
# On-disk discovery + frontmatter parsing
# ---------------------------------------------------------------------------


def test_user_scope_template_loads(
    project_root: Path,
    user_root: Path,
) -> None:
    """Templates under user_root land in the registry with parsed body."""
    body = (
        "---\n"
        "name: review\n"
        "description: Strict reviewer\n"
        "user_prefix: \"Review: \"\n"
        "---\n"
        "You are a meticulous reviewer.\n"
    )
    _write_template(user_root, "review.md", body)
    registry = load_prompt_templates(project_root, user_root=user_root)
    assert "review" in registry
    template = registry["review"]
    assert template.system_prompt == "You are a meticulous reviewer."
    assert template.user_prefix == "Review: "
    assert template.metadata.get("description") == "Strict reviewer"


def test_project_scope_template_loads(
    project_root: Path,
    user_root: Path,
) -> None:
    """Templates under <project>/.weasel/prompts/ land in the registry."""
    body = (
        "---\n"
        "name: tester\n"
        "---\n"
        "You write thorough tests.\n"
    )
    _write_template(project_root / ".weasel" / "prompts", "tester.md", body)
    registry = load_prompt_templates(project_root, user_root=user_root)
    assert "tester" in registry
    assert registry["tester"].system_prompt == "You write thorough tests."


def test_project_scope_overrides_user_scope(
    project_root: Path,
    user_root: Path,
) -> None:
    """Project entries win on name collision."""
    _write_template(
        user_root,
        "shared.md",
        "---\nname: shared\n---\nuser body\n",
    )
    _write_template(
        project_root / ".weasel" / "prompts",
        "shared.md",
        "---\nname: shared\n---\nproject body\n",
    )
    registry = load_prompt_templates(project_root, user_root=user_root)
    assert registry["shared"].system_prompt == "project body"


def test_project_overrides_builtin_default(
    project_root: Path,
    user_root: Path,
) -> None:
    """A project template named ``default`` overrides the built-in."""
    _write_template(
        project_root / ".weasel" / "prompts",
        "default.md",
        "---\nname: default\n---\nproject default body\n",
    )
    registry = load_prompt_templates(project_root, user_root=user_root)
    assert registry["default"].system_prompt == "project default body"


def test_template_without_frontmatter_uses_filename_stem(
    project_root: Path,
    user_root: Path,
) -> None:
    """Files without frontmatter take the file stem as the name."""
    _write_template(user_root, "concise.md", "Be terse.\n")
    registry = load_prompt_templates(project_root, user_root=user_root)
    assert "concise" in registry
    assert registry["concise"].system_prompt == "Be terse."


def test_template_with_metadata_only_no_body(
    project_root: Path,
    user_root: Path,
) -> None:
    """Templates with frontmatter but empty body are still loaded."""
    _write_template(
        user_root,
        "tagged.md",
        "---\nname: tagged\nowner: yad\n---\n",
    )
    registry = load_prompt_templates(project_root, user_root=user_root)
    assert "tagged" in registry
    assert registry["tagged"].system_prompt == ""
    assert registry["tagged"].metadata.get("owner") == "yad"


def test_empty_file_is_skipped(
    project_root: Path,
    user_root: Path,
) -> None:
    """A wholly empty markdown file is skipped, not stored as a no-op."""
    _write_template(user_root, "empty.md", "")
    registry = load_prompt_templates(project_root, user_root=user_root)
    assert "empty" not in registry


def test_filename_stem_used_when_name_missing(
    project_root: Path,
    user_root: Path,
) -> None:
    """Frontmatter without ``name`` falls back to the file stem."""
    body = "---\ndescription: nameless\n---\nbody\n"
    _write_template(user_root, "anonymous.md", body)
    registry = load_prompt_templates(project_root, user_root=user_root)
    assert "anonymous" in registry
    assert registry["anonymous"].metadata.get("description") == "nameless"


def test_metadata_supports_inline_lists_and_bools(
    project_root: Path,
    user_root: Path,
) -> None:
    """Inline-list + boolean frontmatter scalars round-trip."""
    body = (
        "---\n"
        "name: tagged\n"
        "tags: [review, lint]\n"
        "strict: true\n"
        "score: 0.5\n"
        "count: 3\n"
        "---\n"
        "body\n"
    )
    _write_template(user_root, "t.md", body)
    registry = load_prompt_templates(project_root, user_root=user_root)
    template = registry["tagged"]
    assert template.metadata.get("tags") == ["review", "lint"]
    assert template.metadata.get("strict") is True
    assert template.metadata.get("score") == 0.5
    assert template.metadata.get("count") == 3


def test_non_markdown_files_are_ignored(
    project_root: Path,
    user_root: Path,
) -> None:
    """Non-``.md`` files in the prompt dir are skipped."""
    user_root.mkdir(parents=True, exist_ok=True)
    (user_root / "notes.txt").write_text("not a template", encoding="utf-8")
    registry = load_prompt_templates(project_root, user_root=user_root)
    assert "notes" not in registry


def test_hidden_files_are_skipped(
    project_root: Path,
    user_root: Path,
) -> None:
    """Dotfiles in the prompt dir are skipped."""
    user_root.mkdir(parents=True, exist_ok=True)
    (user_root / ".secret.md").write_text("hidden", encoding="utf-8")
    registry = load_prompt_templates(project_root, user_root=user_root)
    assert "secret" not in registry


def test_load_returns_fresh_dict(
    project_root: Path,
    user_root: Path,
) -> None:
    """Mutating the returned dict does not poison subsequent calls."""
    first = load_prompt_templates(project_root, user_root=user_root)
    first["default"] = PromptTemplate(name="default", system_prompt="mutated")
    second = load_prompt_templates(project_root, user_root=user_root)
    assert second["default"].system_prompt != "mutated"


def test_get_with_loaded_registry(
    project_root: Path,
    user_root: Path,
) -> None:
    """``get_prompt_template`` honors a user-supplied loaded registry."""
    body = "---\nname: brief\n---\nKeep replies under 3 lines.\n"
    _write_template(user_root, "brief.md", body)
    registry = load_prompt_templates(project_root, user_root=user_root)
    template = get_prompt_template("brief", registry=registry)
    assert template.system_prompt == "Keep replies under 3 lines."
    # Unknown still falls back to default.
    assert get_prompt_template("missing", registry=registry).name == "default"
