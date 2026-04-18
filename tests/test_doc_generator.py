"""Tests for auto-documentation generator."""
from __future__ import annotations

import os
import tempfile


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

    def test_signature_includes_defaults_and_varargs(self):
        """Signatures must not silently truncate defaults, *args, **kwargs."""
        src = "def fn(a, b=1, *args, c=2, **kwargs): pass\n"
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "m.py"), "w") as f:
                f.write(src)
            gen = DocGenerator(root=tmp)
            sections = gen.scan()
            assert len(sections) >= 1
            # Flatten: module section has the function as a subsection.
            titles = [s.title for s in sections[0].subsections]
            assert len(titles) == 1
            sig = titles[0]
            assert "a" in sig
            assert "b = 1" in sig
            assert "*args" in sig
            assert "c = 2" in sig
            assert "**kwargs" in sig

    def test_signature_includes_annotations_and_return_type(self):
        src = "def f(x: str, y: int = 0) -> list[str]: pass\n"
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "m.py"), "w") as f:
                f.write(src)
            gen = DocGenerator(root=tmp)
            sections = gen.scan()
            sig = sections[0].subsections[0].title
            assert "x: str" in sig
            assert "y: int = 0" in sig
            assert "-> list[str]" in sig

    def test_signature_async_preserved(self):
        src = "async def fetch(url: str) -> bytes: pass\n"
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "m.py"), "w") as f:
                f.write(src)
            gen = DocGenerator(root=tmp)
            sections = gen.scan()
            sig = sections[0].subsections[0].title
            assert sig.startswith("`async ")
            assert "url: str" in sig
            assert "-> bytes" in sig

    def test_signature_keyword_only_args(self):
        src = "def k(*, a: int, b=1): pass\n"
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "m.py"), "w") as f:
                f.write(src)
            gen = DocGenerator(root=tmp)
            sections = gen.scan()
            sig = sections[0].subsections[0].title
            assert "*" in sig
            assert "a: int" in sig
            assert "b = 1" in sig
