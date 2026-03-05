"""Tests for auto-documentation generator."""
from __future__ import annotations

import os
import tempfile

import pytest

from chimera.docs.generator import DocGenerator, DocSection


class TestDocSection:
    def test_to_markdown(self):
        section = DocSection(title="Module", content="Description.")
        md = section.to_markdown()
        assert "# Module" in md
        assert "Description." in md

    def test_nested_markdown(self):
        section = DocSection(
            title="Parent",
            content="",
            subsections=[DocSection(title="Child", content="child text")],
        )
        md = section.to_markdown()
        assert "# Parent" in md
        assert "## Child" in md


class TestDocGenerator:
    def test_scan_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "module.py"), "w") as f:
                f.write('"""Module doc."""\n\ndef hello(name):\n    """Say hello."""\n    pass\n')
            gen = DocGenerator(root=tmp)
            sections = gen.scan()
            assert len(sections) >= 1
            assert sections[0].title == "module"

    def test_scan_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "models.py"), "w") as f:
                f.write('class User:\n    """A user."""\n    def greet(self):\n        """Greet."""\n        pass\n')
            gen = DocGenerator(root=tmp)
            sections = gen.scan()
            assert len(sections) >= 1
            # Should have class subsection
            assert any("User" in s.title for s in sections[0].subsections)

    def test_write_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src")
            os.makedirs(src)
            with open(os.path.join(src, "app.py"), "w") as f:
                f.write('"""App module."""\ndef run(): pass\n')
            out = os.path.join(tmp, "docs")
            gen = DocGenerator(root=src, output_dir=out)
            gen.scan()
            written = gen.write()
            assert len(written) >= 1
            assert os.path.exists(os.path.join(out, "index.md"))

    def test_empty_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            gen = DocGenerator(root=tmp)
            sections = gen.scan()
            assert sections == []

    def test_syntax_error_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "bad.py"), "w") as f:
                f.write("def broken(\n")
            gen = DocGenerator(root=tmp)
            sections = gen.scan()
            assert sections == []

    def test_sections_property(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "x.py"), "w") as f:
                f.write('"""Doc."""\ndef foo(): pass\n')
            gen = DocGenerator(root=tmp)
            gen.scan()
            assert len(gen.sections) >= 1
