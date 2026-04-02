"""Tests for PromptTemplate and PromptTemplateLoader."""

from pathlib import Path

import pytest

from chimera.core.prompt_template import PromptTemplate, PromptTemplateLoader


class TestPromptTemplateRender:
    def test_render_replaces_variables(self):
        t = PromptTemplate(name="greet", content="Hello {{name}}, welcome to {{place}}!")
        result = t.render(name="Alice", place="Wonderland")
        assert result == "Hello Alice, welcome to Wonderland!"

    def test_render_uses_defaults(self):
        t = PromptTemplate(
            name="greet",
            content="Hello {{name}}!",
            variables={"name": "World"},
        )
        result = t.render()
        assert result == "Hello World!"

    def test_render_kwargs_override_defaults(self):
        t = PromptTemplate(
            name="greet",
            content="Hello {{name}}!",
            variables={"name": "World"},
        )
        result = t.render(name="Alice")
        assert result == "Hello Alice!"

    def test_render_removes_unreplaced(self):
        t = PromptTemplate(name="partial", content="Hello {{name}}, your id is {{id}}.")
        result = t.render(name="Bob")
        assert result == "Hello Bob, your id is ."


class TestPromptTemplateFromFile:
    def test_from_file_with_frontmatter(self, tmp_path: Path):
        md = tmp_path / "review.md"
        md.write_text(
            "---\n"
            "name: code-review\n"
            "description: Review code changes\n"
            "variables:\n"
            "  language: python\n"
            "  style: concise\n"
            "---\n"
            "Review this {{language}} code in a {{style}} manner.\n"
        )
        t = PromptTemplate.from_file(md)
        assert t.name == "code-review"
        assert t.description == "Review code changes"
        assert t.variables == {"language": "python", "style": "concise"}
        assert "Review this" in t.content
        assert t.source_path == md

    def test_from_file_plain_markdown(self, tmp_path: Path):
        md = tmp_path / "simple.md"
        md.write_text("Just a plain template with {{var}}.")
        t = PromptTemplate.from_file(md)
        assert t.name == "simple"
        assert t.content == "Just a plain template with {{var}}."
        assert t.variables == {}
        assert t.source_path == md


class TestPromptTemplateLoader:
    def test_loader_finds_templates(self, tmp_path: Path):
        prompts_dir = tmp_path / ".chimera" / "prompts"
        prompts_dir.mkdir(parents=True)

        (prompts_dir / "alpha.md").write_text(
            "---\nname: alpha\ndescription: first\n---\nAlpha content."
        )
        (prompts_dir / "beta.md").write_text("Beta plain content.")

        loader = PromptTemplateLoader(search_paths=[tmp_path])
        templates = loader.load_all()

        assert "alpha" in templates
        assert templates["alpha"].description == "first"
        assert "beta" in templates
        assert templates["beta"].content == "Beta plain content."

    def test_loader_get_returns_template(self, tmp_path: Path):
        prompts_dir = tmp_path / ".chimera" / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "gamma.md").write_text(
            "---\nname: gamma\n---\nGamma body."
        )

        loader = PromptTemplateLoader(search_paths=[tmp_path])
        t = loader.get("gamma")
        assert t is not None
        assert t.name == "gamma"

    def test_loader_get_returns_none_for_missing(self, tmp_path: Path):
        loader = PromptTemplateLoader(search_paths=[tmp_path])
        assert loader.get("nonexistent") is None
